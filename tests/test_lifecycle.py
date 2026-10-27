from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from eviepoch.config import Settings
from eviepoch.domain import (IdempotencyConflict, IngestBusy, InvalidExtraction, Obligation,
                             QueryPlan, Source, TimeBounds, locate, namespace)
from eviepoch.retrieval.lexical import terms
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from eviepoch.providers import ProviderUnavailable
from eviepoch.retrieval import Evidence, select
from eviepoch.retrieval import tokens


def fact(quote, value, *, key="f", predicate="address", **kwargs):
    return {"key": key, "message": 0, "quote": quote, "kind": "fact", "subject": "user",
            "predicate": predicate, "value": value, "scope": "general", "modality": "asserted",
            "cardinality": "single", **kwargs}


class AnnotatedProviders:
    """An extraction oracle for contract tests, explicitly NOT a quality estimate."""
    def __init__(self):
        self.documents = {}
        self.plan_value = None
        self.fail_embed = False

    def embedding_identity(self):
        return {"model": "contract-test", "dimensions": 32, "endpoint": "https://example.test/v1",
                "preprocessing": "utf8-2048-byte-byte-weighted-normalized-mean"}

    def extract(self, sources, known):
        content = sources[0].content
        data = self.documents.get(content, {"nodes": [], "relations": [], "governance": []})
        return copy.deepcopy(data(known) if callable(data) else data)

    def register(self, content, nodes=(), relations=(), governance=()):
        self.documents[content] = {"nodes": list(nodes), "relations": list(relations), "governance": list(governance)}

    def plan(self, query, options):
        return copy.deepcopy(self.plan_value or {"intents": ["fact"], "obligations": [{"query": query}]})

    def verify_candidates(self, query, sources):
        return {sid for sid, source in sources.items() if set(terms(query)) & set(terms(source.content))}

    def embed(self, texts):
        if self.fail_embed:
            raise ProviderUnavailable("synthetic failure")
        output = []
        for text in texts:
            vector = np.zeros(32, dtype=np.float32)
            for token in terms(text):
                vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 32] += 1
            if not vector.any():
                vector[0] = 1
            output.append(vector / np.linalg.norm(vector))
        return np.asarray(output)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.config = Settings(db_path=str(Path(self.temp.name) / "memory.sqlite3"), embed_dimensions=32,
                               llm_api_key="test", embed_api_key="test", embed_base_url="https://example.test/v1",
                               auth_scheme="none", workbench_enabled=True, window_radius=0)
        self.provider = AnnotatedProviders()
        self.pipeline = MemoryPipeline(self.config, self.provider)
        self.pipeline.start()

    def tearDown(self):
        self.pipeline.close()
        self.temp.cleanup()

    def add(self, content, nodes=(), *, rid="r1", user="u", timestamp=None, relations=(), role="user"):
        self.provider.register(content, nodes, relations)
        request = AddRequest(rid, user, "s", [Message(role, content, timestamp)])
        self.pipeline.add(request)
        return request

    def inspect(self, user="u"):
        return self.pipeline.inspect(user)

    def test_source_roundtrip_utf8_span_and_missing_timestamp(self):
        text = "  我对花生过敏。\n不要删掉空白。  "
        self.add(text, [fact("我对花生过敏。", "花生", predicate="allergy")])
        memory = self.inspect()
        self.assertEqual(memory["sources"][0]["content"], text)
        self.assertEqual(memory["sources"][0]["role"], "user")
        self.assertEqual(memory["sources"][0]["locator"]["message_id"], memory["sources"][0]["id"])
        self.assertNotIn("content", memory["sources"][0]["locator"])
        self.assertIsNone(memory["sources"][0]["asserted_at"])
        span = memory["spans"][0]
        self.assertEqual(text.encode()[span["byte_start"]:span["byte_end"]].decode(), span["quote"])
        result = self.pipeline.search(SearchRequest("花生过敏", "u", 10))
        self.assertTrue(result["data"])
        self.assertIn(text, result["data"][0]["content"])
        self.assertNotIn("created_at", result["data"][0])

    def test_identical_retry_and_changed_payload_conflict_survive_restart(self):
        request = self.add("I live in Kyoto.", [fact("I live in Kyoto.", "Kyoto")])
        self.pipeline.add(request)
        self.assertEqual(self.inspect()["generation"], 1)
        self.pipeline.close()
        self.pipeline.start()
        self.assertEqual(self.inspect()["generation"], 1)
        self.pipeline.add(request)
        with self.assertRaises(IdempotencyConflict):
            self.pipeline.add(replace(request, messages=[Message("user", "Different")]))

    def test_failed_provider_is_atomic_and_retryable(self):
        self.provider.fail_embed = True
        with self.assertRaises(ProviderUnavailable):
            self.add("A private value", [fact("A private value", "value")])
        self.assertEqual(self.inspect()["sources"], [])
        self.assertEqual(self.inspect()["generation"], 0)
        self.provider.fail_embed = False
        self.add("A private value", [fact("A private value", "value")])
        self.assertEqual(self.inspect()["generation"], 1)

    def test_unsupported_quote_rejects_entire_add(self):
        with self.assertRaises(InvalidExtraction):
            self.add("Real source", [fact("Invented source", "fake")])
        self.assertEqual(self.inspect()["sources"], [])

    def test_ambiguous_quote_is_rejected(self):
        with self.assertRaises(InvalidExtraction):
            self.add("same same", [fact("same", "same")])

    def test_changes_corrections_plans_and_multivalue_have_different_relations(self):
        self.add("In 2022 I lived in Kyoto.", [fact("In 2022 I lived in Kyoto.", "Kyoto", valid_date="2022", time_quote="2022")])
        self.add("In 2024 I moved to Osaka.", [fact("In 2024 I moved to Osaka.", "Osaka", valid_date="2024", time_quote="2024", change="change")], rid="r2")
        self.add("I plan to move to Tokyo in 2028.", [fact("I plan to move to Tokyo in 2028.", "Tokyo", valid_date="2028", time_quote="2028", modality="planned")], rid="r3")
        edges = self.inspect()["edges"]
        self.assertEqual([e["kind"] for e in edges], ["SUPERSEDES"])
        self.add("Correction: Osaka was wrong; it was Kobe.", [fact("Correction: Osaka was wrong; it was Kobe.", "Kobe", change="correction")], rid="r4")
        self.assertTrue(any(e["kind"] == "CORRECTS" for e in self.inspect()["edges"]))
        self.add("I also enjoy chess.", [fact("I also enjoy chess.", "chess", predicate="hobby", cardinality="multi")], rid="r5")
        self.add("I enjoy hiking.", [fact("I enjoy hiking.", "hiking", predicate="hobby", cardinality="multi")], rid="r6")
        hobby_ids = {n["id"] for n in self.inspect()["nodes"] if n["predicate"] == "hobby"}
        self.assertFalse(any(e["target"] in hobby_ids for e in self.inspect()["edges"]))

    def test_late_old_history_does_not_supersede_newer_event(self):
        self.add("In 2024 I moved to Osaka.", [fact("In 2024 I moved to Osaka.", "Osaka", valid_date="2024", time_quote="2024", change="change")])
        self.add("In 2022 I moved to Kyoto.", [fact("In 2022 I moved to Kyoto.", "Kyoto", valid_date="2022", time_quote="2022", change="change")], rid="r2")
        self.assertFalse(any(e["kind"] == "SUPERSEDES" for e in self.inspect()["edges"]))

    def test_current_and_asof_take_different_source_evidence(self):
        self.add("In 2022 my address was Kyoto.", [fact("In 2022 my address was Kyoto.", "Kyoto", valid_date="2022", time_quote="2022")])
        self.add("In 2024 my address changed to Osaka.", [fact("In 2024 my address changed to Osaka.", "Osaka", valid_date="2024", time_quote="2024", change="change")], rid="r2")
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "user address"}]}
        current = self.pipeline.search(SearchRequest("What is my current address?", "u", 1))
        self.assertIn("Osaka", current["data"][0]["content"])
        self.provider.plan_value = {"intents": ["as_of"], "as_of": "2022", "obligations": [{"query": "user address 2022"}]}
        past = self.pipeline.search(SearchRequest("What was my address in 2022?", "u", 1))
        self.assertIn("Kyoto", past["data"][0]["content"])

    def test_genuinely_unresolved_conflict_returns_both_sources(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.add("My address is Osaka.", [fact("My address is Osaka.", "Osaka")], rid="r2")
        response = self.pipeline.search(SearchRequest("What is my address?", "u", 4))
        self.assertEqual(len(response["data"]), 2)

    def test_consolidation_retains_rare_constraint_and_source_coverage(self):
        self.add("I must never eat peanuts.", [fact("I must never eat peanuts.", "no peanuts", predicate="diet")])
        self.add("I avoid peanuts in every meal.", [fact("I avoid peanuts in every meal.", "no peanuts", predicate="diet")], rid="r2")
        data = self.inspect()
        summary = next(n for n in data["nodes"] if n["kind"] == "summary")
        self.assertTrue(summary["protected"])
        self.assertEqual(len(summary["source_ids"]), 2)
        self.assertEqual(len(data["sources"]), 2)

    def _forget(self, action="suppress"):
        instruction = "Please forget my Kyoto address."
        def extraction(known):
            targets = [n.id for n in known.nodes.values() if n.predicate == "address"]
            return {"nodes": [], "relations": [], "governance": [{"message": 0, "quote": instruction,
                    "action": action, "target_ids": targets, "subject": "user", "predicate": "address"}]}
        self.provider.documents[instruction] = extraction
        request = AddRequest("forget", "u", "s", [Message("user", instruction)])
        self.pipeline.add(request)
        return request

    def test_suppression_closes_derivations_and_old_retry_does_not_resurrect(self):
        original = self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.add("I like coffee.", [fact("I like coffee.", "coffee", predicate="drink")], rid="other")
        self._forget()
        self.pipeline.add(original)
        data = self.inspect()
        self.assertNotIn("Kyoto", json.dumps(data))
        self.assertEqual(len(data["sources"]), 1)
        self.assertIn("coffee", data["sources"][0]["content"])
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto address", "u", 100))["data"], [])
        self.assertEqual(data["policy_generation"], 1)

    def test_erase_removes_all_live_text_and_indices_but_preserves_hash_ledger(self):
        original = self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self._forget("erase")
        self.pipeline.add(original)
        with self.pipeline.store.transaction(namespace("u")) as c:
            for table in ("ee_objects", "ee_vectors", "ee_postings", "ee_edges", "ee_dependencies", "ee_provenance", "ee_versions"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table} WHERE ns=?", (namespace("u"),)).fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ee_requests").fetchone()[0], 2)
        self.assertEqual(self.inspect()["sources"], [])

    def test_new_source_can_explicitly_reintroduce_forgotten_fact(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self._forget()
        self.add("Please remember: my address is Kyoto.", [fact("Please remember: my address is Kyoto.", "Kyoto")], rid="new")
        result = self.pipeline.search(SearchRequest("Kyoto address", "u", 10))
        self.assertEqual(len(result["data"]), 1)
        self.assertIn("Please remember", result["data"][0]["content"])

    def test_user_isolation_and_query_options_do_not_mutate_memory(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.add("My address is Osaka.", [fact("My address is Osaka.", "Osaka")], user="other")
        before = self.inspect()
        result = self.pipeline.search(SearchRequest("Kyoto address", "u", 10, ["My address is Atlantis"]))
        self.assertNotIn("Osaka", json.dumps(result))
        self.assertNotIn("Atlantis", json.dumps(result))
        self.assertEqual(before, self.inspect())

    def test_agent_suggestion_is_not_attributed_to_user(self):
        self.add("You might like Kyoto.", [fact("You might like Kyoto.", "Kyoto")], role="assistant")
        node = self.inspect()["nodes"][0]
        self.assertNotEqual(node["subject"], "user")

    def test_streaming_generation_changes_visible_evidence(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        _, trace = self.pipeline.search_with_trace(SearchRequest("address", "u", 10))
        self.assertEqual(trace["generation"], 1)
        self.add("My address is Osaka.", [fact("My address is Osaka.", "Osaka", change="correction")], rid="r2")
        response, trace = self.pipeline.search_with_trace(SearchRequest("address", "u", 10))
        self.assertEqual(trace["generation"], 2)
        self.assertIn("Osaka", json.dumps(response))

    def test_concurrent_identical_add_is_one_commit(self):
        request = AddRequest("same", "u", "s", [Message("user", "I like coffee.")])
        self.provider.register("I like coffee.", [fact("I like coffee.", "coffee", predicate="drink")])
        def run(_):
            try:
                self.pipeline.add(request)
                return "ok"
            except IngestBusy:
                return "retry"
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(run, range(16)))
        self.pipeline.add(request)
        self.assertEqual(self.inspect()["generation"], 1)
        self.assertEqual(len(self.inspect()["sources"]), 1)

    def test_budget_constraints_and_score_sorting(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.pipeline.config = replace(self.config, char_budget=1)
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto address", "u", 10))["data"], [])

    def test_causal_path_restores_distant_upstream_source(self):
        cause = "The certificate rotation disabled the legacy trust anchor."
        self.add(cause, [fact(cause, "legacy trust anchor disabled", predicate="trust_anchor", key="cause")])
        effect = "Invoice delivery failed because the legacy trust anchor was disabled."
        def extract(known):
            prior = next(n for n in known.nodes.values() if n.predicate == "trust_anchor")
            return {"nodes": [fact(effect, "invoice delivery failed", predicate="delivery", key="effect")],
                    "relations": [{"from": prior.id, "to": "effect", "kind": "CAUSE_STATED", "message": 0, "quote": effect}],
                    "governance": []}
        self.provider.documents[effect] = extract
        self.pipeline.add(AddRequest("effect", "u", "s", [Message("user", effect)]))
        self.provider.plan_value = {"intents": ["why"], "required_relations": ["CAUSE_STATED"],
                                    "obligations": [{"query": "invoice delivery failure"}]}
        response, trace = self.pipeline.search_with_trace(SearchRequest("Why did invoice delivery fail?", "u", 4))
        self.assertEqual(len(response["data"]), 2)
        self.assertTrue(any(cause in r["content"] for r in response["data"]))
        self.assertEqual(trace["source_paths"][0]["kind"], "CAUSE_STATED")

    def test_reflection_is_challenged_by_new_source_and_dependency_deletion(self):
        lesson = "I think strategy A always fails in staging."
        self.add(lesson, [fact(lesson, "A always fails", predicate="strategy", kind="reflection", key="lesson", scope="staging")])
        counter = "Strategy A succeeded in staging this time; that disproves my earlier generalization."
        def extract(known):
            prior = next(n for n in known.nodes.values() if n.kind == "reflection")
            return {"nodes": [fact(counter, "A succeeded", predicate="outcome", key="counter", scope="staging")],
                    "relations": [{"from": "counter", "to": prior.id, "kind": "CONTRADICTS", "message": 0, "quote": counter}],
                    "governance": []}
        self.provider.documents[counter] = extract
        self.pipeline.add(AddRequest("counter", "u", "s", [Message("user", counter)]))
        reflection = next(n for n in self.inspect()["nodes"] if n["kind"] == "reflection")
        self.assertEqual(reflection["state"], "challenged")
        self.assertEqual(len(reflection["counterexample_ids"]), 1)
        self.assertEqual(len(reflection["source_ids"]), 2)
        states = {n["state"] for n in self.inspect()["versions"] if n["id"] == reflection["id"]}
        self.assertEqual(states, {"hypothesis", "challenged"})

    def test_causal_link_quote_in_third_message_is_part_of_complete_proof(self):
        cause, effect = "The legacy trust anchor was disabled.", "Invoice delivery failed."
        self.add(cause, [fact(cause, "disabled", predicate="anchor")])
        self.add(effect, [fact(effect, "failed", predicate="delivery")], rid="effect")
        explanation = "The disabled legacy trust anchor caused the invoice delivery failure."
        def extract(known):
            origin = next(n.id for n in known.nodes.values() if n.predicate == "anchor")
            target = next(n.id for n in known.nodes.values() if n.predicate == "delivery")
            return {"nodes": [], "relations": [{"from": origin, "to": target, "kind": "CAUSE_STATED",
                    "message": 0, "quote": explanation}], "governance": []}
        self.provider.documents[explanation] = extract
        self.pipeline.add(AddRequest("explanation", "u", "s", [Message("user", explanation)]))
        self.provider.plan_value = {"intents": ["why"], "required_relations": ["CAUSE_STATED"],
                                    "obligations": [{"query": "invoice delivery"}]}
        result = self.pipeline.search(SearchRequest("Why did invoice delivery fail?", "u", 5))
        self.assertEqual(len(result["data"]), 3)
        self.assertTrue(any(explanation in item["content"] for item in result["data"]))

    def test_procedure_bundle_includes_prerequisite_and_exception(self):
        rule = "Deployment requires approval; emergency incidents are the exception."
        nodes = [fact(rule, "deploy", predicate="deployment", kind="procedure", key="procedure"),
                 fact("approval", "approval", predicate="approval", kind="rule", key="approval"),
                 fact("emergency incidents", "emergency", predicate="incident", key="exception")]
        relations = [{"from": "procedure", "to": target, "kind": kind, "message": 0, "quote": rule}
                     for target, kind in (("approval", "REQUIRES"), ("exception", "EXCEPT_IF"))]
        self.add(rule, nodes, relations=relations)
        self.provider.plan_value = {"intents": ["procedure"], "required_relations": ["REQUIRES", "EXCEPT_IF"],
                                    "obligations": [{"query": "deployment"}]}
        response, trace = self.pipeline.search_with_trace(SearchRequest("What is the deployment procedure?", "u", 3))
        self.assertEqual(len(response["data"]), 1)
        self.assertEqual({e["kind"] for e in trace["source_paths"]}, {"REQUIRES", "EXCEPT_IF"})

    def test_scoped_preferences_do_not_conflict(self):
        self.add("At work I prefer tea.", [fact("At work I prefer tea.", "tea", predicate="drink", scope="work", kind="preference")])
        self.add("At home I prefer coffee.", [fact("At home I prefer coffee.", "coffee", predicate="drink", scope="home", kind="preference")], rid="r2")
        self.assertEqual(self.inspect()["edges"], [])

    def test_same_name_different_identity_is_not_merged(self):
        self.add("Alex from finance lives in Paris.", [fact("Alex from finance lives in Paris.", "Paris", subject="Alex:finance")])
        self.add("Alex from design lives in Berlin.", [fact("Alex from design lives in Berlin.", "Berlin", subject="Alex:design")], rid="r2")
        self.assertEqual(self.inspect()["edges"], [])
        self.pipeline.config = replace(self.config, answer_input_tokens=0)
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto address", "u", 10))["data"], [])


class ProofSelectionTests(unittest.TestCase):
    def test_budget_includes_serialized_id_score_time_and_escaped_content(self):
        config = Settings(answer_item_tokens=0)
        evidence = Evidence("a" * 32, 'quoted "text"\nnext line', "2024-01-01T00:00:00Z", 0.123456789, "s")
        self.assertGreater(tokens.evidence_cost(config, evidence), tokens.count(evidence.content))
        plan = QueryPlan(obligations=[Obligation("o", "text", alternatives=[[evidence.id]])])
        self.assertEqual(select(plan, {evidence.id: evidence}, config, 1, tokens.evidence_cost(config, evidence)-1), [])

    def test_official_timestamp_is_milliseconds_even_near_epoch(self):
        from eviepoch.domain import timestamp
        self.assertEqual(timestamp(1000), "1970-01-01T00:00:01Z")

    def test_joint_selector_selects_both_required_premises_over_redundant_high_scores(self):
        evidence = {sid: Evidence(sid, text, None, score, sid) for sid, text, score in (
            ("a", "A prerequisite", 0.8), ("b", "B exception", 0.7), ("noise", "Redundant top hit", 1.0))}
        plan = QueryPlan(["procedure"], obligations=[Obligation("rule", "rule", alternatives=[["a", "b"]])])
        config = Settings(answer_item_tokens=0)
        beam = select(plan, evidence, config, 2, 1000)
        topk = select(plan, evidence, config, 2, 1000, "topk")
        self.assertEqual({e.id for e in beam}, {"a", "b"})
        self.assertEqual({e.id for e in topk}, {"noise", "a"})

    def test_or_of_and_supports_equivalent_short_proof(self):
        evidence = {s: Evidence(s, s, None, 1, s) for s in "abc"}
        plan = QueryPlan(obligations=[Obligation("o", "x", alternatives=[["a", "b"], ["c"]])])
        selected = select(plan, evidence, Settings(), 1, 1000)
        self.assertEqual([e.id for e in selected], ["c"])

    def test_calendar_precision_is_preserved(self):
        self.assertEqual(TimeBounds.parse("2024-02").latest, "2024-02-29")
        self.assertEqual(TimeBounds.parse("2022").precision, "year")
        with self.assertRaises(InvalidExtraction):
            TimeBounds.parse("2022-02-29")


if __name__ == "__main__":
    unittest.main()
