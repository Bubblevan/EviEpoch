"""Semantic packing regressions with independent synthetic data, never private fixtures."""
import json
import unittest
from dataclasses import replace

import test_lifecycle as fixtures
from eviepoch.domain import Source
from eviepoch.domain.requests import SearchRequest
from eviepoch.ingest.temporal import message_anchor
from eviepoch.providers import ProviderUnavailable
from eviepoch.retrieval.context import worked_example_for_reference
from experiments.evidence_metrics import evidence_metrics, evidence_sufficiency


class PackingControls(fixtures.LifecycleTests):
    def test_invalid_excerpt_gets_one_repair_with_unchanged_exact_validation(self):
        self.add("Public meeting is Friday.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [{"id": e.id, "quotes": ["invented text"]} for e in es]}
        repairs = []
        def repair(q, p, es, previous, error):
            repairs.append(error)
            return {"decisions": [{"id": e.id, "mode": "retain", "quotes": []} for e in es]}
        self.provider.repair_disclosure = repair
        self.assertTrue(self.pipeline.search(SearchRequest("public meeting", "u", 5))["data"])
        self.assertEqual(len(repairs), 1)

    def test_a_failed_repair_is_bounded_and_does_not_publish_bad_quotes(self):
        self.add("Public meeting is Friday.")
        bad = lambda es: {"decisions": [{"id": e.id, "quotes": ["invented text"]} for e in es]}
        self.provider.disclose_evidence = lambda q, p, es: bad(es)
        repairs = []
        def repair(q, p, es, previous, error):
            repairs.append(error)
            return bad(es)
        self.provider.repair_disclosure = repair
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("public meeting", "u", 5))
        self.assertEqual(len(repairs), 1)

    def test_temporal_derivation_is_not_removed_by_a_direct_slot_audit(self):
        self.add("Casey: I have owned the bicycle for three years.", timestamp=1613295000000)
        self.provider.audit_attribute_support = lambda *args: {"status": "insufficient", "ids": []}
        result = self.pipeline.search(SearchRequest("Which year did Casey acquire the bicycle?", "u", 5))
        self.assertTrue(result["data"])
    def test_explicit_document_page_does_not_require_generated_facts_or_quotes(self):
        text = "[archive][session_1][D1:1][text] document: Document: synthetic-notes.pdf\nNative clip page: 1 of 2\n\n```text\nPublic meeting: Friday.\n```"
        def unexpected(*args):
            raise AssertionError("explicit document navigation must not call neural extraction")
        self.provider.extract = unexpected
        self.add(text)
        state = self.inspect()
        self.assertEqual(state["sources"][0]["content"], text)
        self.assertFalse(any(n["kind"] == "fact" for n in state["nodes"]))
        result = self.pipeline.search(SearchRequest("Public meeting", "u", 5))
        self.assertTrue(result["data"])
    def test_exact_display_envelope_can_be_removed_without_rewriting_source(self):
        text = "Casey: I joined yesterday."
        self.add(text, timestamp=1613295000000)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": [e.content]} for e in es]}
        result = self.pipeline.search(SearchRequest("When did Casey join?", "u", 5))
        self.assertIn(text, result["data"][0]["content"])

    def test_invented_display_envelope_is_not_stripped(self):
        self.add("Casey: I joined yesterday.", timestamp=1613295000000)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ["[1900-01-01][user] Casey: I joined yesterday."]} for e in es]}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("When did Casey join?", "u", 5))

    def test_final_attribute_audit_does_not_treat_a_name_as_an_unstated_size(self):
        self.add("Taylor works in design.")
        self.provider.audit_evidence = lambda q, p, units: {"status": "insufficient", "ids": []}
        query = SearchRequest("Taylor shoe size", "u", 5)
        response, trace = self.pipeline.search_with_trace(query)
        self.assertEqual(response["data"], [])
        self.assertEqual(trace["selection_status"], "no_attribute_support")
        self.pipeline.config = replace(self.config, support_audit_enabled=False)
        self.assertTrue(self.pipeline.search(query)["data"])

    def test_final_attribute_audit_cannot_invent_a_source_id(self):
        self.add("Taylor works in design.")
        self.provider.audit_evidence = lambda q, p, units: {"status": "relevant", "ids": ["fabricated"]}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("Taylor design", "u", 5))

    def test_unresolved_answer_does_not_turn_relevant_conflict_into_a_503(self):
        self.add("The workshop room is north.")
        self.provider.audit_attribute_support = lambda q, p, units: {"status": "uncertain", "ids": list(units)}
        result = self.pipeline.search(SearchRequest("workshop room", "u", 5))
        self.assertTrue(result["data"])

    def test_uncertainty_without_supported_ids_is_not_silent_absence(self):
        self.add("The workshop room is north.")
        self.provider.audit_attribute_support = lambda q, p, units: {"status": "uncertain", "ids": []}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("workshop room", "u", 5))
    def test_scoped_worked_example_keeps_operations_not_just_an_id(self):
        record = {"dataset": "TOY", "task_index": 7, "trajectory": [
            {"role": "user", "content": "Move the fictional blue block to shelf A."},
            {"role": "assistant", "tool_calls": [{"function": {"name": "move_block", "arguments": '{"block":"blue","target":"A"}'}}]},
            {"role": "tool", "content": "Moved successfully."}]}
        text = "Worked example:\n" + json.dumps(record)
        self.add(text)
        repairs = []
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "excerpt", "quotes": ['"task_index": 7']} for e in es]}
        def repair(q, p, es, previous, reason):
            repairs.append(reason)
            return {"decisions": [{"id": e.id, "mode": "retain", "quotes": []} for e in es]}
        self.provider.repair_disclosure = repair
        result, trace = self.pipeline.search_with_trace(SearchRequest("TOY task index 99", "u", 10))
        self.assertEqual(len(repairs), 1)
        self.assertEqual(trace["worked_example_candidates"], 1)
        self.assertIn(text, result["data"][0]["content"])
        self.assertIn("move_block", result["data"][0]["content"])
        self.assertIn("Moved successfully", result["data"][0]["content"])

    def test_metadata_only_worked_example_is_not_a_successful_procedure(self):
        text = "Worked example:\n" + json.dumps({"dataset": "TOY", "task_index": 7, "trajectory": [{"content": "Move blue block."}]})
        self.add(text)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ['"task_index": 7']} for e in es]}
        with self.assertRaises(ProviderUnavailable) as failure:
            self.pipeline.search(SearchRequest("TOY task index 99", "u", 10))
        self.assertEqual(failure.exception.stage, "disclosure_sufficiency")

    def test_retention_does_not_reserialize_scientific_observations(self):
        text = "Synthetic measurements:\ntime_s\tuptake_mg_g\n0\t0.0\n10\t4.5\n30\t8.0\nCondition: 295 K."
        self.add(text)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "retain", "quotes": []} for e in es]}
        result, trace = self.pipeline.search_with_trace(SearchRequest("Compare synthetic uptake measurements", "u", 5))
        self.assertTrue(result["data"])
        self.assertIn(text, result["data"][0]["content"])
        self.assertTrue(trace["covered_obligations"])

    def test_global_measurements_do_not_turn_into_a_cherry_picked_row(self):
        text = r"Measurements\ntime_s\tuptake_mg_g\n0\t0.0\n10\t4.5\n30\t8.0\n60\t8.3"
        self.add(text)
        self.provider.plan_value = {"intents": ["global"], "obligations": [{"query": "measurements uptake"}]}
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "excerpt", "quotes": [r"10\t4.5"]} for e in es]}
        result = self.pipeline.search(SearchRequest("Compare measurements uptake trend", "u", 5))
        self.assertTrue(any(text in e["content"] for e in result["data"]))

    def test_full_retention_cannot_override_a_current_query_disclosure_policy(self):
        self.add("Planning: public meeting Friday. Restricted marker: violet.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "retain", "quotes": []} for e in es]}
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("Planning overview; do not disclose restricted markers.", "u", 5))

    def test_exclusion_keeps_unknown_attribute_empty(self):
        self.add("Casey works in design.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "exclude", "quotes": []} for e in es]}
        self.assertEqual(self.pipeline.search(SearchRequest("Casey shoe size", "u", 5))["data"], [])

    def test_restricted_excerpt_does_not_restore_hidden_author_or_time_metadata(self):
        self.add("[Time: Feb 14, 2021, 09:30:00] Secret Author: Public meeting is Friday.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "mode": "excerpt", "quotes": ["Public meeting is Friday."]} for e in es]}
        result = self.pipeline.search(SearchRequest("Public meeting; do not disclose author or source timestamps.", "u", 5))
        self.assertIn("Friday", json.dumps(result))
        self.assertNotIn("Secret Author", json.dumps(result))
        self.assertNotIn("2021-02-14", json.dumps(result))
        self.assertNotIn("created_at", result["data"][0])

    def test_human_readable_message_time_survives_minimal_quote(self):
        text = "[Time: Feb 14, 2021, 09:30:00] Casey: I enrolled yesterday."
        self.add(text)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ["I enrolled yesterday."]} for e in es]}
        result = self.pipeline.search(SearchRequest("When did Casey enroll?", "u", 5))
        self.assertEqual(result["data"][0]["created_at"], "2021-02-14T09:30:00")
        self.assertIn("Casey:", result["data"][0]["content"])

    def test_original_time_is_primary_and_transport_time_remains_labeled(self):
        text = "[archive][session_1][D1:1][text] Casey: I enrolled yesterday.\n2020-03-01 09:15"
        self.add(text, timestamp=1704153600000)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ["I enrolled yesterday."]} for e in es]}
        result = self.pipeline.search(SearchRequest("When did Casey enroll?", "u", 5))
        self.assertEqual(result["data"][0]["created_at"], "2020-03-01T09:15:00")
        self.assertTrue(result["data"][0]["content"].startswith("[2020-03-01T09:15:00]"))
        self.assertIn("transport_time=2024-01-02", result["data"][0]["content"])
        self.assertIn("Casey:", result["data"][0]["content"])


for _name in vars(fixtures.LifecycleTests):
    if _name.startswith("test_"):
        setattr(PackingControls, _name, None)


class MetricAndRoutingControls(unittest.TestCase):
    def test_source_match_does_not_prove_a_metadata_fragment_is_sufficient(self):
        groups, ids = [{"source-a"}], {"source-a"}
        response = [{"id": "span-a", "content": '"task_index": 7'}]
        self.assertTrue(evidence_metrics(groups, ids)[0])
        self.assertIsNone(evidence_sufficiency(groups, ids, response))
        self.assertFalse(evidence_sufficiency(groups, ids, response, ["move_block"]))

    def test_whole_source_and_declared_quote_controls_are_separate(self):
        self.assertTrue(evidence_sufficiency([{"s"}], {"s"}, [{"id": "s", "content": "all content"}]))
        self.assertTrue(evidence_sufficiency([{"s"}], {"s"}, [{"id": "fragment", "content": "blue block moved"}], ["block moved"]))

    def test_worked_example_route_requires_scope_and_an_opaque_reference(self):
        source = Source("s", "n", "r", "session", 0, "user", json.dumps({"dataset": "TOY", "trajectory": [{"content": "move"}]}), None, "ignored")
        self.assertTrue(worked_example_for_reference("TOY task index 99", source))
        self.assertFalse(worked_example_for_reference("OTHER task index 99", source))
        self.assertFalse(worked_example_for_reference("What result did task 99 actually achieve?", source))

    def test_old_transport_only_anchor_can_be_reparsed_from_authoritative_bytes(self):
        from eviepoch.ingest.temporal import anchor_for
        source = Source("s", "n", "r", "session", 0, "user", "[Time: Feb 14, 2021, 09:30:00] Casey: Hello.", None, "ignored",
                        locator={"temporal_anchor": {"kind": "transport", "time": None}})
        self.assertEqual(anchor_for(source)["time"], "2021-02-14T09:30:00")


if __name__ == "__main__":
    unittest.main()
