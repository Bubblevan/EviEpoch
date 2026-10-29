"""Independent synthetic controls. No private evaluation content or quality claims."""
import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import test_lifecycle as fixtures
from eviepoch.domain import InvalidExtraction, Node, QueryPlan, Snapshot, Source, TimeBounds, namespace, timestamp
from eviepoch.domain.requests import AddRequest, Message, SearchRequest
from eviepoch.ingest.extraction import EXTRACTION_PROMPT, PLAN_PROMPT, parse_plan
from eviepoch.ingest.temporal import message_anchor, relative_bounds
from eviepoch.manage.temporal import project_query_state
from eviepoch.providers import Providers, ProviderUnavailable, StructuredOutputInvalid
from eviepoch.retrieval.windows import context_windows


def triple(subject, relation, value, sid, rid, vid, update=False):
    prefix = "Corpus: " + ("UPDATE: replace the prior value for this subject and relation.\n" if update else "")
    return prefix + json.dumps(dict(subject=subject, relation=relation, object=value,
                                   subject_id=sid, relation_id=rid, object_id=vid))


class ImprovementControls(fixtures.LifecycleTests):
    def test_query_plan_prompt_uses_single_supported_order_axis(self):
        self.assertIn('"order_by":"event"', PLAN_PROMPT)
        self.assertIn("Set order_by to exactly one of event, first_mention, or record.", PLAN_PROMPT)
        self.assertNotIn('"order_by":"event|first_mention|record"', PLAN_PROMPT)
        plan = parse_plan({"intents": ["fact"], "order_by": "event", "obligations": []}, "query")
        self.assertEqual(plan.order_by, "event")

    def test_temporal_anchor_echo_is_metadata_not_a_source_quote(self):
        content = "The event was confirmed."
        anchor = timestamp(1704067200000)
        self.assertIn("Never copy its normalized timestamp into time_quote.", EXTRACTION_PROMPT)
        self.add(content, [fixtures.fact(content, "confirmed", predicate="status", kind="event",
                                         event_date="2024-01-01", time_quote=anchor)],
                 rid="anchor-echo", timestamp=1704067200000)
        node = next(n for n in self.inspect()["nodes"] if n["kind"] == "event")
        self.assertEqual(node["occurrence_time"]["earliest"], "2024-01-01")

    def test_non_anchor_time_quote_still_fails_exact_source_validation(self):
        content = "The event was confirmed."
        with self.assertRaises(InvalidExtraction):
            self.add(content, [fixtures.fact(content, "confirmed", predicate="status", kind="event",
                                             event_date="2024-01-01", time_quote="not a source quote")],
                     rid="invalid-time-quote", timestamp=1704067200000)

    def test_repair_prompt_receives_the_invalid_draft_as_untrusted_context(self):
        request = AddRequest("repair-context", "u", "s", [Message("user", "Synthetic fact is teal.")])
        sources = self.pipeline._build_sources(request, namespace("u"), "2026-01-01T00:00:00Z")
        previous = {"nodes": [{"quote": "not in source"}], "relations": [], "governance": []}
        captured = {}
        provider = Providers(self.config)

        def capture(system, user, *, stage):
            captured.update(system=system, user=json.loads(user), stage=stage)
            return {"nodes": [], "relations": [], "governance": []}

        with patch.object(provider, "_structured", side_effect=capture):
            provider.repair_extraction(sources, Snapshot(0, 0, {}, {}, []),
                                       InvalidExtraction("quote is absent from the authoritative source"),
                                       previous=previous)
        self.assertEqual(captured["stage"], "extract_repair")
        self.assertEqual(captured["user"]["previous_invalid_output"], previous)
        self.assertEqual(captured["user"]["validation_error"],
                         "quote is absent from the authoritative source")
        self.assertIn("untrusted model-generated data", captured["system"])

    def test_raw_ingest_fallback_keeps_source_searchable_without_unverified_nodes(self):
        self.pipeline.config = replace(self.config, raw_ingest_fallback=True,
                                       structured_ingest_enabled=False, proof_binding_enabled=False,
                                       gap_iterations=0)

        def fail_extract(_sources, _known):
            raise ProviderUnavailable("synthetic extraction timeout", stage="extract")

        self.provider.extract = fail_extract
        content = "Synthetic preference is teal."
        self.pipeline.add(AddRequest("raw-fallback", "u", "s", [Message("user", content)]))
        stored = self.inspect()
        self.assertEqual([source["content"] for source in stored["sources"]], [content])
        self.assertEqual(stored["nodes"], [])
        result = self.pipeline.search(SearchRequest("teal preference", "u", 5))
        self.assertTrue(result["data"])
        self.assertIn(content, result["data"][0]["content"])

    def test_raw_ingest_fallback_is_disabled_by_default(self):
        self.assertFalse(self.config.raw_ingest_fallback)

    def test_cross_user_model_stages_overlap(self):
        for user in ("u", "v"):
            self.add("Synthetic preference is teal.", user=user)
        lock, entered, release = threading.Lock(), threading.Event(), threading.Event()
        active = 0
        def plan(query, options):
            nonlocal active
            with lock:
                active += 1
                if active == 2:
                    entered.set()
            release.wait(3)
            return {"intents": ["fact"], "obligations": [{"query": query}]}
        self.provider.plan = plan
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.pipeline.search, SearchRequest("preference teal", user, 3)) for user in ("u", "v")]
            overlapped = entered.wait(2)
            release.set()
            results = [f.result(timeout=5) for f in futures]
        self.assertTrue(overlapped, "different users serialized while a model stage was waiting")
        self.assertTrue(all(r["data"] for r in results))

    def test_forget_during_planning_invalidates_old_snapshot(self):
        self.add("My address is Kyoto.", [fixtures.fact("My address is Kyoto.", "Kyoto")])
        entered, release = threading.Event(), threading.Event()
        def plan(query, options):
            entered.set()
            release.wait(3)
            return {"intents": ["fact"], "obligations": [{"query": query}]}
        self.provider.plan = plan
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(self.pipeline.search_with_trace, SearchRequest("Kyoto address", "u", 3))
            self.assertTrue(entered.wait(2))
            forgotten = pool.submit(self._forget)
            try:
                forgotten.result(timeout=2)
            finally:
                release.set()
            response, trace = pending.result(timeout=5)
        self.assertEqual(response["data"], [])
        self.assertEqual(trace["policy_generation"], 1)
        self.assertEqual(trace["consistency_retries"], 1)

    def test_continuously_changing_namespace_fails_boundedly(self):
        self.add("Initial synthetic note.")
        calls = []
        def plan(query, options):
            calls.append(1)
            self.add(f"Another synthetic note {len(calls)}.", rid=f"concurrent-{len(calls)}")
            return {"intents": ["fact"], "obligations": [{"query": query}]}
        self.provider.plan = plan
        with self.assertRaises(ProviderUnavailable) as failure:
            self.pipeline.search(SearchRequest("synthetic note", "u", 4))
        self.assertEqual(failure.exception.stage, "read_consistency")
        self.assertEqual(len(calls), self.config.search_consistency_retries + 1)

    def test_original_time_overrides_transport_for_relative_event_only(self):
        text = "[archive][session_1][D1:2][text] Mira: I joined the club yesterday.\n2020-03-01 09:15"
        self.add(text, [fixtures.fact("I joined the club yesterday.", "joined", predicate="club", kind="event",
                     event_date="2024-01-01", time_quote="yesterday")], timestamp=1704153600000)
        data = self.inspect()
        node = next(n for n in data["nodes"] if n["kind"] == "event")
        self.assertEqual(node["occurrence_time"]["earliest"], "2020-02-29")
        self.assertIsNone(node["valid_time"]["earliest"])
        self.assertTrue(data["sources"][0]["asserted_at"].startswith("2024-01-02"))
        result = self.pipeline.search(SearchRequest("joined club", "u", 3))
        self.assertIn("original_message_time=2020-03-01", result["data"][0]["content"])

    def test_ambiguous_original_times_do_not_yield_a_precise_relative_date(self):
        text = "[Time: 2020-02-01] [Time: 2021-02-01] I joined yesterday."
        self.add(text, [fixtures.fact("I joined yesterday.", "joined", event_date="2024-01-01", time_quote="yesterday")],
                 timestamp=1704153600000)
        node = next(n for n in self.inspect()["nodes"] if n["kind"] == "fact")
        self.assertIsNone(node["occurrence_time"]["earliest"])

    def test_structured_updates_keep_current_chain_and_historical_sources(self):
        rows = [triple("Orion", "director", "Lena", "company-7", "director", "person-1"),
                triple("Lena", "residence", "Westport", "person-1", "residence", "place-1"),
                triple("Orion", "director", "Niko", "company-7", "director", "person-2", True),
                triple("Niko", "residence", "Eastport", "person-2", "residence", "place-2")]
        for i, text in enumerate(rows):
            self.add(text, rid=f"structured-{i}", timestamp=1577836800000 + i * 86400000)
        state = self.inspect()
        self.assertEqual(len(state["sources"]), 4)
        self.assertTrue(any(e["kind"] == "CORRECTS" for e in state["edges"]))
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "Orion director"}, {"query": "Niko residence"}]}
        current = self.pipeline.search(SearchRequest("Where does the Orion director live?", "u", 10))
        rendered = json.dumps(current)
        self.assertIn("Niko", rendered)
        self.assertIn("Eastport", rendered)
        self.assertNotIn('"object\\\": \\\"Lena', rendered)
        self.assertFalse(any(rows[0] in e["content"] for e in current["data"]))
        self.provider.plan_value = {"intents": ["history"], "obligations": [{"query": "Orion director"}]}
        history = self.pipeline.search(SearchRequest("Orion director history", "u", 10))
        self.assertTrue(any(rows[0] in e["content"] for e in history["data"]))

    def test_same_label_different_structured_identity_is_not_replaced(self):
        old = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        new = triple("Orion", "director", "Niko", "company-2", "director", "person-2", True)
        self.add(old)
        self.add(new, rid="other-company")
        self.assertFalse(any(e["kind"] == "CORRECTS" for e in self.inspect()["edges"]))

    def test_equal_value_labels_with_different_ids_are_distinct_replacements(self):
        self.add(triple("Orion", "director", "Alex", "company-1", "director", "person-1"))
        self.add(triple("Orion", "director", "Alex", "company-1", "director", "person-2", True), rid="replacement")
        self.assertTrue(any(e["kind"] == "CORRECTS" for e in self.inspect()["edges"]))

    def test_structured_asof_query_cannot_use_later_replacement(self):
        old = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        new = triple("Orion", "director", "Niko", "company-1", "director", "person-2", True)
        self.add(old, timestamp=1577836800000)
        self.add(new, rid="replacement", timestamp=1578009600000)
        self.provider.plan_value = {"intents": ["as_of"], "as_of": "2020-01-01", "obligations": [{"query": "Orion director"}]}
        result = self.pipeline.search(SearchRequest("Orion director on 2020-01-01", "u", 10))
        self.assertTrue(any(old in e["content"] for e in result["data"]))
        self.assertFalse(any(new in e["content"] for e in result["data"]))

    def test_late_arriving_older_record_does_not_reverse_explicit_replacement(self):
        new = triple("Orion", "director", "Niko", "company-1", "director", "person-2", True)
        old = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        self.add(new, timestamp=1578009600000)
        self.add(old, rid="late-old", timestamp=1577836800000)
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "Orion director"}]}
        result = self.pipeline.search(SearchRequest("Current Orion director", "u", 10))
        self.assertTrue(any(new in e["content"] for e in result["data"]))
        self.assertFalse(any(old in e["content"] for e in result["data"]))
        self.provider.plan_value = {"intents": ["as_of"], "as_of": "2020-01-01", "obligations": [{"query": "Orion director"}]}
        historical = self.pipeline.search(SearchRequest("Orion director on 2020-01-01", "u", 10))
        self.assertTrue(any(old in e["content"] for e in historical["data"]))

    def test_structured_plan_is_not_promoted_to_a_current_replacement(self):
        old = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        self.add(old)
        plan = dict(subject="Orion", relation="director", object="Niko", subject_id="company-1",
                    relation_id="director", object_id="person-2", operation="replace", modality="planned", valid_date="2030")
        self.add(json.dumps(plan), rid="planned")
        self.assertFalse(any(e["kind"] == "CORRECTS" for e in self.inspect()["edges"]))
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "Orion director"}]}
        result = self.pipeline.search(SearchRequest("Current Orion director", "u", 10))
        self.assertTrue(any(old in e["content"] for e in result["data"]))
        self.assertFalse(any("Niko" in e["content"] for e in result["data"]))

    def test_structured_source_bypasses_model_but_ordinary_prose_does_not(self):
        text = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        def fail(*args):
            raise AssertionError("model should not process a supported structured assertion")
        self.provider.extract = fail
        self.add(text)
        self.assertEqual(self.inspect()["generation"], 1)
        with self.assertRaises(AssertionError):
            self.add("Imagine this JSON was a fact: " + text, rid="prose")
        self.assertEqual(self.inspect()["generation"], 1)

    def test_identity_follow_retrieves_beyond_surface_query(self):
        root = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        tail = triple("Completely different label", "residence", "Eastport", "person-1", "residence", "place-1")
        self.add(root)
        self.add(tail, rid="tail")
        with self.pipeline.store.view(namespace("u")) as view:
            roots = [n for n in view.snapshot().nodes.values() if n.subject_id == "company-1"]
            linked = view.follow(roots)
        self.assertTrue(any(n.value == "Eastport" for n in linked.nodes.values()))

    def test_global_coverage_recovers_each_session_and_respects_budget(self):
        for i in range(5):
            text = f"Garden milestone phase {i}: synthetic detail {i}."
            self.pipeline.add(AddRequest(f"phase-{i}", "u", f"session-{i}", [Message("user", text)]))
        self.provider.plan_value = {"intents": ["global"], "obligations": [{"query": "garden milestone"}]}
        response, trace = self.pipeline.search_with_trace(SearchRequest("Summarize the garden milestones", "u", 10))
        self.assertEqual(len(response["data"]), 5)
        self.assertEqual(trace["coverage_candidates"], 5)
        self.assertFalse(trace["missing_obligations"])
        limited, trace = self.pipeline.search_with_trace(SearchRequest("Summarize the garden milestones", "u", 2))
        self.assertEqual(len(limited["data"]), 2)
        self.assertTrue(trace["missing_obligations"])

    def test_single_factor_coverage_control_holds_model_query_and_budget_fixed(self):
        for i in range(5):
            self.pipeline.add(AddRequest(f"phase-{i}", "u", f"session-{i}", [Message("user", f"Garden milestone {i}.")]))
        self.provider.plan_value = {"intents": ["global"], "obligations": [{"query": "garden milestone"}]}
        query = SearchRequest("Garden milestone overview", "u", 10)
        with_coverage = self.pipeline.search(query)
        self.pipeline.config = replace(self.config, coverage_enabled=False)
        without_coverage = self.pipeline.search(query)
        self.assertEqual(len(with_coverage["data"]), 5)
        self.assertLess(len(without_coverage["data"]), 5)
        self.assertEqual(self.pipeline.config.answer_input_tokens, self.config.answer_input_tokens)

    def test_single_factor_state_control_does_not_change_stored_history(self):
        old = triple("Orion", "director", "Lena", "company-1", "director", "person-1")
        new = triple("Orion", "director", "Niko", "company-1", "director", "person-2", True)
        self.add(old)
        self.add(new, rid="replacement")
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "Orion director"}]}
        before = copy.deepcopy(self.inspect())
        query = SearchRequest("Current Orion director", "u", 10)
        projected, projected_trace = self.pipeline.search_with_trace(query)
        self.pipeline.config = replace(self.config, state_projection_enabled=False)
        unprojected, unprojected_trace = self.pipeline.search_with_trace(query)
        self.assertFalse(any(old in e["content"] for e in projected["data"]))
        self.assertTrue(any(new in e["content"] for e in unprojected["data"]))
        self.assertGreater(unprojected_trace["candidates"], projected_trace["candidates"])
        self.assertEqual(self.inspect(), before)

    def test_order_uses_original_record_times(self):
        for i, day in enumerate(("2020-02-03", "2020-02-01", "2020-02-02")):
            text = f"[archive][session_{i}][D{i}:1][text] Garden milestone {i}.\n{day}"
            self.add(text, rid=f"phase-{i}")
        self.provider.plan_value = {"intents": ["order"], "obligations": [{"query": "garden milestone"}]}
        result = self.pipeline.search(SearchRequest("Garden milestone order", "u", 10))
        contents = [row["content"] for row in result["data"]]
        self.assertIn("2020-02-01", contents[0])
        self.assertIn("2020-02-03", contents[-1])

    def test_event_order_and_mention_order_use_different_axes(self):
        first = "Garden milestone occurred in 2023."
        second = "Garden milestone occurred in 2022."
        self.add(first, [fixtures.fact(first, "milestone 2023", predicate="garden", kind="event",
                                     event_date="2023", time_quote="2023")], timestamp=1704067200000)
        self.add(second, [fixtures.fact(second, "milestone 2022", predicate="garden", kind="event",
                                      event_date="2022", time_quote="2022")], rid="later-record", timestamp=1735689600000)
        self.provider.plan_value = {"intents": ["order"], "order_by": "event", "obligations": [{"query": "garden milestone"}]}
        event_order = self.pipeline.search(SearchRequest("Garden milestone order", "u", 5))
        self.provider.plan_value["order_by"] = "first_mention"
        mention_order = self.pipeline.search(SearchRequest("Garden milestone order", "u", 5))
        self.assertIn(second, event_order["data"][0]["content"])
        self.assertIn(first, mention_order["data"][0]["content"])

    def test_disclosure_excludes_forbidden_quote_but_keeps_permitted_source(self):
        text = "Planning notes:\nRestricted detail is violet.\nPublic schedule is Friday."
        self.add(text)
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ["Public schedule is Friday."]} for e in es]}
        response, trace = self.pipeline.search_with_trace(SearchRequest("Give the public schedule from planning notes", "u", 10))
        self.assertEqual(len(response["data"]), 1)
        self.assertNotIn("violet", json.dumps(response))
        self.assertIn("Friday", json.dumps(response))
        self.assertEqual(trace["selection_status"], "disclosed_spans")
        self.assertTrue(trace["missing_obligations"], "an excerpt is not automatically a complete source proof")

    def test_disclosure_trims_only_an_unmatched_terminal_quote_delimiter(self):
        self.add("Public schedule is Friday. The archive contains a later quote mark: \".")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ['Public schedule is Friday."']} for e in es]}
        response = self.pipeline.search(SearchRequest("Public schedule", "u", 5))
        self.assertTrue(response["data"][0]["content"].endswith("Public schedule is Friday."))

    def test_unknown_attribute_can_remove_irrelevant_biography(self):
        self.add("Rae enjoys sailing and works in design.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [{"id": e.id, "quotes": []} for e in es]}
        response, trace = self.pipeline.search_with_trace(SearchRequest("Rae's shoe size", "u", 5))
        self.assertEqual(response["data"], [])
        self.assertEqual(trace["selection_status"], "no_disclosable_support")

    def test_disclosure_rejects_fabricated_quotes_and_missing_decisions(self):
        self.add("Public schedule is Friday.")
        for bad in [lambda es: {"decisions": []},
                    lambda es: {"decisions": [{"id": es[0].id, "quotes": ["Public schedule is Sunday."]}]},
                    lambda es: {"decisions": [{"id": "fabricated", "quotes": []}]}]:
            self.provider.disclose_evidence = lambda q, p, es, make=bad: make(es)
            with self.assertRaises(ProviderUnavailable):
                self.pipeline.search(SearchRequest("public schedule", "u", 5))

    def test_single_factor_disclosure_control_preserves_the_same_public_fact(self):
        self.add("Private marker is violet. Public schedule is Friday.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [
            {"id": e.id, "quotes": ["Public schedule is Friday."]} for e in es]}
        query = SearchRequest("Public schedule", "u", 5)
        minimal = self.pipeline.search(query)
        self.pipeline.config = replace(self.config, disclosure_enabled=False)
        whole = self.pipeline.search(query)
        self.assertIn("Friday", json.dumps(minimal))
        self.assertIn("Friday", json.dumps(whole))
        self.assertNotIn("violet", json.dumps(minimal))
        self.assertIn("violet", json.dumps(whole))

    def test_final_disclosure_budget_is_reapplied_after_quote_splitting(self):
        self.add("Schedule is Friday.\nLocation is north.\nDuration is two hours.")
        self.provider.disclose_evidence = lambda q, p, es: {"decisions": [{"id": e.id, "quotes": [
            "Schedule is Friday.", "Location is north.", "Duration is two hours."]} for e in es]}
        result = self.pipeline.search(SearchRequest("schedule location duration", "u", 1))
        self.assertEqual(len(result["data"]), 1)

    def test_tool_trajectory_retains_raw_source_and_procedure_navigation(self):
        data = {"dataset": "synthetic_tools", "task_index": 12, "trajectory": [
            {"role": "user", "content": "Sort a fictional catalog."},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "sort_catalog", "arguments": "{}"}}]},
            {"role": "tool", "content": "Sorted successfully."}], "eval_feedback": {"reward": 1}}
        text = "Completed tool trajectory:\n" + json.dumps(data)
        self.add(text)
        state = self.inspect()
        self.assertEqual(state["sources"][0]["content"], text)
        self.assertTrue(any(n["kind"] == "procedure" and "sort_catalog" in n["value"] for n in state["nodes"]))
        result = self.pipeline.search(SearchRequest("sort catalog for a new task 99", "u", 5))
        self.assertTrue(result["data"], "a different task ID must not hard-filter a transferable example")

    def test_invalid_json_can_split_and_recover_without_partial_publication(self):
        calls = []
        def extract(sources, known):
            calls.append(1)
            if sum(len(s.content) for s in sources) > 700:
                raise StructuredOutputInvalid("synthetic malformed output")
            return {"nodes": [fixtures.fact(s.content, s.content, message=i, key=f"f{i}", quote_occurrence=0)
                              for i, s in enumerate(sources)], "relations": [], "governance": []}
        self.provider.extract = extract
        text = "Synthetic multilingual material 例外。" * 80
        self.add(text)
        self.assertGreater(len(calls), 1)
        self.assertEqual(self.inspect()["generation"], 1)
        self.assertEqual(self.inspect()["sources"][0]["content"], text)

    def test_coverage_cap_is_reported_as_incomplete(self):
        for i in range(5):
            self.add(f"Garden milestone {i}.", rid=f"phase-{i}")
        self.provider.plan_value = {"intents": ["global"], "obligations": [{"query": "garden milestone"}]}
        self.pipeline.config = replace(self.config, coverage_source_limit=2)
        _, trace = self.pipeline.search_with_trace(SearchRequest("Garden milestone overview", "u", 10))
        self.assertTrue(trace["coverage_truncated"])
        self.assertIn("coverage_remainder", trace["missing_obligations"])

    def test_global_request_without_a_shared_topic_can_seed_history(self):
        for i, text in enumerate(("A ferry arrived.", "A sapling grew.", "A kettle whistled.")):
            self.add(text, rid=f"phase-{i}")
        self.provider.plan_value = {"intents": ["global"], "obligations": [{"query": "overview"}]}
        self.provider.verify_candidates = lambda query, sources: set(sources)
        result = self.pipeline.search(SearchRequest("Overview", "u", 10))
        self.assertEqual(len(result["data"]), 3)

    def test_episode_navigation_never_becomes_generated_api_evidence(self):
        self.add("Garden milestone: the sapling survived.", [fixtures.fact(
            "Garden milestone: the sapling survived.", "sapling survived", predicate="garden")])
        state = self.inspect()
        episode = next(n for n in state["nodes"] if n["kind"] == "episode")
        self.assertEqual(episode["state"], "navigation")
        result = self.pipeline.search(SearchRequest("garden milestone", "u", 5))
        self.assertTrue(result["data"])
        self.assertTrue(all(e["id"] != episode["id"] for e in result["data"]))
        self.assertTrue(all("source_navigation" not in e["content"] for e in result["data"]))

    def test_forget_invalidates_episode_derived_from_multiple_sources(self):
        address = "My address is Kyoto."
        self.provider.register(address, [fixtures.fact(address, "Kyoto")])
        self.pipeline.add(AddRequest("mixed", "u", "session", [Message("user", address), Message("user", "I enjoy pottery.")]))
        episodes = [n for n in self.inspect()["nodes"] if n["kind"] == "episode"]
        self.assertEqual(len(episodes[0]["source_ids"]), 2)
        self._forget()
        state = self.inspect()
        self.assertNotIn("Kyoto", json.dumps(state))
        self.assertTrue(any("pottery" in s["content"] for s in state["sources"]))


for _name in vars(fixtures.LifecycleTests):
    if _name.startswith("test_"):
        setattr(ImprovementControls, _name, None)


class IsolatedControls(unittest.TestCase):
    def test_approximate_relative_time_stays_an_interval(self):
        bounds = relative_bounds("大概半个月前", "2020-03-01T09:00:00")
        self.assertEqual((bounds.earliest, bounds.latest, bounds.precision), ("2020-02-10", "2020-02-20", "approximate"))

    def test_prose_event_date_is_not_original_message_metadata(self):
        source = Source("s", "n", "r", "session", 0, "user", "I plan to travel on 2020-01-02.", "2021-02-03", "ignored")
        self.assertEqual(message_anchor(source)["kind"], "transport")

    def test_tail_exception_and_relevant_rule_are_in_source_exact_windows(self):
        text = "unrelated padding\n" * 500 + "Catalog may be sorted after validation.\nException: never sort locked entries.\n"
        windows = context_windows(text, "catalog sorting validation", 800)
        rendered = "".join(w["text"] for w in windows)
        self.assertIn("never sort locked entries", rendered)
        self.assertIn("after validation", rendered)
        self.assertLessEqual(len(rendered), 800)
        self.assertTrue(all(text[w["start"]:w["end"]] == w["text"] for w in windows))

    def test_single_long_line_keeps_distant_exception(self):
        text = "Catalog sorting requires validation. " + "padding " * 1000 + "Exception: never sort locked entries."
        windows = context_windows(text, "catalog sorting validation", 800)
        rendered = "".join(w["text"] for w in windows)
        self.assertIn("requires validation", rendered)
        self.assertIn("never sort locked entries", rendered)
        self.assertLessEqual(len(rendered), 800)

    def test_invalid_json_response_has_a_typed_bounded_ingest_failure(self):
        from eviepoch.config import Settings
        provider = Providers(Settings(llm_api_key="synthetic"))
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{broken"))], usage=None)
        provider._client = lambda _: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
        with self.assertRaises(StructuredOutputInvalid):
            provider._structured("synthetic", "synthetic", stage="extract")

    def test_bad_embedding_is_a_provider_failure_not_a_client_request_error(self):
        from eviepoch.config import Settings
        provider = Providers(Settings(embed_dimensions=32))
        response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0])], usage=None)
        provider._client = lambda _: SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: response))
        with self.assertRaises(ProviderUnavailable) as failure:
            provider.embed(["synthetic"])
        self.assertEqual(failure.exception.stage, "embedding_validation")

    def test_diagnostics_do_not_emit_private_ids_or_trace_content(self):
        from eviepoch.observability.diagnostics import log_search_result
        trace = {"binding_status": "bound", "selection_status": "proof_selected", "selected_ids": ["PRIVATE-ID"],
                 "covered_obligations": ["private-obligation"], "missing_obligations": [],
                 "plan": {"query": "PRIVATE TEXT"}, "source_paths": ["PRIVATE PATH"], "timings_ms": {"binding": 12}}
        with self.assertLogs("eviepoch.diagnostics", level="INFO") as captured:
            log_search_result(trace)
        payload = "".join(captured.output)
        self.assertNotIn("PRIVATE", payload)
        self.assertNotIn("private-obligation", payload)
        self.assertIn('"selected_ids_count":1', payload)


if __name__ == "__main__":
    unittest.main()
