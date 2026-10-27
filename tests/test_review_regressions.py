"""Adversarial review controls; annotations are test oracles, not quality estimates."""
import unittest
from dataclasses import replace

import test_lifecycle as fixtures
from test_lifecycle import AnnotatedProviders, fact
from eviepoch.domain import InvalidExtraction
from eviepoch.models import SearchRequest
from eviepoch.providers import ProviderUnavailable
from eviepoch.providers import Providers, StructuredOutputTruncated
from eviepoch.models import AddRequest, Message


class BindingFaults(AnnotatedProviders):
    def __init__(self):
        super().__init__()
        self.binding = "empty"
        self.audit = "relevant"

    def bind_obligations(self, query, plan, snapshot):
        if self.binding == "invalid":
            return {"obligations": [{"id": plan.obligations[0].id, "alternatives": [["invented"]]}]}
        groups = [list(snapshot.sources)] if self.binding == "oversize" else []
        return {"obligations": [{"id": o.id, "alternatives": groups} for o in plan.obligations]}

    def audit_evidence(self, query, plan, sources):
        if self.audit == "unavailable":
            raise ProviderUnavailable("synthetic audit outage")
        if self.audit == "invalid":
            return {"status": "relevant", "ids": ["invented"]}
        ids = sorted(self.verify_candidates(query, sources)) if self.audit == "relevant" else []
        return {"status": "relevant" if ids else "insufficient", "ids": ids}


class BindingRegressionTests(fixtures.LifecycleTests):
    # Inherit setup/helpers, not the original lifecycle cases a second time.
    def setUp(self):
        super().setUp()
        self.provider = BindingFaults()
        self.pipeline.providers = self.provider

    def test_empty_binding_with_verified_raw_support_recovers_evidence(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        result, trace = self.pipeline.search_with_trace(SearchRequest("Kyoto address", "u", 3))
        self.assertTrue(result["data"])
        self.assertEqual(trace["selection_status"], "verified_fallback")
        self.assertEqual(trace["missing_obligations"], ["o1"])

    def test_empty_binding_and_no_support_remain_empty(self):
        self.add("I enjoy pottery.", [fact("I enjoy pottery.", "pottery", predicate="hobby")])
        result, trace = self.pipeline.search_with_trace(SearchRequest("bank account number", "u", 3))
        self.assertEqual(result["data"], [])
        self.assertEqual(trace["selection_status"], "no_support_in_candidates")

    def test_invalid_binding_is_atomic_and_can_recover_from_raw_support(self):
        self.provider.binding = "invalid"
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        result, trace = self.pipeline.search_with_trace(SearchRequest("Kyoto address", "u", 3))
        self.assertTrue(result["data"])
        self.assertEqual(trace["binding_status"], "invalid")

    def test_malformed_llm_schema_recovers_without_server_type_errors(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        cases = [[], {"obligations": ["wrong"]}, {"obligations": [{"id": ["o1"], "alternatives": []}]},
                 {"obligations": [{"id": "o1", "alternatives": [[["wrong"]]]}]}]
        for value in cases:
            with self.subTest(value=value):
                self.provider.bind_obligations = lambda *args, data=value: data
                result, trace = self.pipeline.search_with_trace(SearchRequest("Kyoto address", "u", 3))
                self.assertTrue(result["data"])
                self.assertEqual(trace["binding_status"], "invalid")

    def test_unaffordable_and_proof_can_return_explicit_partial_support(self):
        self.add("Kyoto address was my old address.", [fact("Kyoto address was my old address.", "Kyoto")])
        self.add("Osaka address is my new address.", [fact("Osaka address is my new address.", "Osaka")], rid="r2")
        self.provider.binding = "oversize"
        result, trace = self.pipeline.search_with_trace(SearchRequest("address", "u", 1))
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(trace["covered_obligations"], [])
        self.assertEqual(trace["selection_status"], "verified_fallback")

    def test_binding_and_audit_outage_are_not_reported_as_no_evidence(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.provider.audit = "unavailable"
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("Kyoto address", "u", 3))

    def test_audit_cannot_invent_or_resurrect_suppressed_ids(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.provider.audit = "invalid"
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.search(SearchRequest("Kyoto address", "u", 3))
        self._forget()
        self.provider.audit = "relevant"
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto address", "u", 3))["data"], [])

    def test_fallback_never_promotes_a_future_plan_to_current_state(self):
        text = "I plan to live in Kyoto in 2028."
        self.add(text, [fact(text, "Kyoto", modality="planned", valid_date="2028", time_quote="2028")])
        self.provider.plan_value = {"intents": ["current"], "obligations": [{"query": "Kyoto address"}]}
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto address", "u", 3))["data"], [])

    def test_replacement_span_does_not_claim_complete_source_level_proof(self):
        quote = "My address is Kyoto."
        self.add("noise " * 1000 + quote, [fact(quote, "Kyoto")])
        self.provider.binding = "oversize"
        self.pipeline.config = replace(self.config, char_budget=200)
        result, trace = self.pipeline.search_with_trace(SearchRequest("Kyoto address", "u", 3))
        self.assertTrue(result["data"])
        self.assertEqual(trace["covered_obligations"], [])
        self.assertEqual(trace["selection_status"], "verified_fallback")


# unittest's inherited cases otherwise run with an intentionally faulty binder.
for _name in list(vars(fixtures.LifecycleTests)):
    if _name.startswith("test_"):
        setattr(BindingRegressionTests, _name, None)


class TemporalRegressionTests(fixtures.LifecycleTests):
    def test_misclassified_correction_cannot_revoke_disjoint_history(self):
        old = "In 2022 my address was Kyoto."
        self.add(old, [fact(old, "Kyoto", valid_date="2022", time_quote="2022")])
        new = "In 2024 I moved to Osaka."
        self.add(new, [fact(new, "Osaka", change="correction", valid_date="2024", time_quote="2024")], rid="r2")
        state = self.inspect()
        self.assertFalse(any(e["kind"] == "CORRECTS" for e in state["edges"]))
        self.provider.plan_value = {"intents": ["as_of"], "as_of": "2022", "obligations": [{"query": "2022 address"}]}
        result = self.pipeline.search(SearchRequest("address in 2022", "u", 5))
        self.assertTrue(any(old in row["content"] for row in result["data"]))
        self.assertEqual(len(state["sources"]), 2)

    def test_true_correction_is_targeted_to_one_assertion(self):
        for rid, year, value in (("old", "2022", "Kyoto"), ("later", "2024", "Osaka")):
            text = f"In {year} my address was {value}."
            self.add(text, [fact(text, value, valid_date=year, time_quote=year)], rid=rid)
        prior = next(n for n in self.inspect()["nodes"] if n["kind"] == "fact" and n["value"] == "Osaka")
        self.provider.verify_changes = lambda proposed, sources, known: {"decisions": [
            {"id": n.id, "classification": "correction", "target_ids": [prior["id"]]} for n in proposed]}
        text = "Correction: the address I gave for 2024 was Kobe, not Osaka."
        self.add(text, [fact(text, "Kobe", change="correction", valid_date="2024", time_quote="2024")], rid="correct")
        edges = [e for e in self.inspect()["edges"] if e["kind"] == "CORRECTS"]
        self.assertEqual([e["target"] for e in edges], [prior["id"]])

    def test_unknown_review_keeps_raw_sources_and_no_correction_edge(self):
        self.add("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        self.provider.verify_changes = lambda proposed, sources, known: {"decisions": [
            {"id": n.id, "classification": "uncertain", "target_ids": []} for n in proposed]}
        self.add("Now I moved to Osaka.", [fact("Now I moved to Osaka.", "Osaka", change="correction")], rid="r2")
        self.assertEqual(len(self.inspect()["sources"]), 2)
        self.assertFalse(any(e["kind"] == "CORRECTS" for e in self.inspect()["edges"]))


for _name in list(vars(fixtures.LifecycleTests)):
    if _name.startswith("test_"):
        setattr(TemporalRegressionTests, _name, None)


class WindowProvider(AnnotatedProviders):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.always_truncate = False
        self.bad_quotes = False
        self.truncate_above = 700

    def extract(self, sources, known):
        self.calls += 1
        if self.always_truncate or len(sources) > 1 or sum(len(s.content) for s in sources) > self.truncate_above:
            raise StructuredOutputTruncated("synthetic output cap")
        nodes = []
        for i, source in enumerate(sources):
            quote = "invented quote" if self.bad_quotes else source.content
            nodes.append(fact(quote, source.content, key=f"f{i}", predicate="excerpt", cardinality="multi",
                              message=i, quote_occurrence=0))
        return {"nodes": nodes, "relations": [], "governance": []}


class LongAddRegressionTests(fixtures.LifecycleTests):
    def setUp(self):
        super().setUp()
        self.provider = WindowProvider()
        self.pipeline.providers = self.provider
        self.pipeline.config = replace(self.config, extraction_chars=2048)

    def test_repeated_nonenglish_long_source_has_exact_window_offsets(self):
        text = ("重复内容。花生过敏。مرحبا بالعالم。日本語の記録。\n" * 180) + "尾部罕见约束：禁止药品 X。"
        request = AddRequest("long", "u", "s", [Message("user", text)])
        self.pipeline.add(request)
        state = self.inspect()
        self.assertEqual(state["sources"][0]["content"], text)
        for span in state["spans"]:
            self.assertEqual(text.encode()[span["byte_start"]:span["byte_end"]].decode(), span["quote"])
        self.assertTrue(any("禁止药品 X" in span["quote"] for span in state["spans"]))
        self.assertLessEqual(self.provider.calls, self.pipeline.config.extraction_max_calls)

    def test_multimessage_splitting_preserves_roles_ordinals_and_all_sources(self):
        messages = [Message("user" if i % 2 == 0 else "assistant", f"turn {i}: " + "界" * 120) for i in range(8)]
        self.pipeline.add(AddRequest("many", "u", "s", messages))
        sources = sorted(self.inspect()["sources"], key=lambda s: s["ordinal"])
        self.assertEqual([(s["role"], s["content"]) for s in sources], [(m.role, m.content) for m in messages])
        self.assertGreater(self.provider.calls, 1)

    def test_persistent_truncation_is_bounded_atomic_and_retryable(self):
        self.provider.always_truncate = True
        self.pipeline.config = replace(self.pipeline.config, extraction_split_depth=2, extraction_max_calls=6)
        request = AddRequest("retry", "u", "s", [Message("user", "长文本" * 1000)])
        with self.assertRaises(ProviderUnavailable):
            self.pipeline.add(request)
        self.assertEqual(self.inspect()["sources"], [])
        self.assertEqual(self.inspect()["generation"], 0)
        self.assertLessEqual(self.provider.calls, 6)
        self.provider.always_truncate = False
        self.pipeline.config = replace(self.pipeline.config, extraction_split_depth=4, extraction_max_calls=128)
        self.pipeline.add(request)
        self.assertEqual(self.inspect()["generation"], 1)

    def test_persistent_bad_quotes_never_commit_partial_sources(self):
        self.provider.bad_quotes = True
        with self.assertRaises(InvalidExtraction):
            self.pipeline.add(AddRequest("bad", "u", "s", [Message("user", "长文本" * 1000)]))
        self.assertEqual(self.inspect()["sources"], [])

    def test_provider_truncation_remains_a_typed_retry_signal(self):
        from types import SimpleNamespace as S
        p = Providers(replace(self.config, llm_api_key="test"))
        response = S(usage=None, choices=[S(finish_reason="length", message=S(content="{"))])
        p._chat_client = S(chat=S(completions=S(create=lambda **kwargs: response)))
        with self.assertRaises(StructuredOutputTruncated):
            p._structured("JSON", "public")

    def test_two_hundred_thousand_characters_are_not_truncated_in_storage(self):
        import numpy as np
        self.provider.truncate_above = 12000
        self.provider.embed = lambda texts: np.tile(np.asarray([1.] + [0.]*31, dtype=np.float32), (len(texts), 1))
        self.pipeline.config = replace(self.config, extraction_chars=24000)
        text = ("重复的长来源界。" * 26000) + "仅出现一次的罕见约束：禁止药品 X。"
        self.assertGreater(len(text), 200000)
        self.pipeline.add(AddRequest("very-long", "u", "s", [Message("user", text)]))
        state = self.inspect()
        self.assertEqual(state["sources"][0]["content"], text)
        self.assertTrue(any("禁止药品 X" in s["quote"] for s in state["spans"]))

    def test_input_token_preflight_does_not_call_provider_for_oversized_payload(self):
        from eviepoch.domain import Snapshot, Source
        from types import SimpleNamespace as S
        p = Providers(replace(self.config, llm_api_key="test", extraction_input_tokens=2048))
        p._chat_client = S(chat=S(completions=S(create=lambda **kwargs: self.fail("oversized input reached the provider"))))
        source = Source("a"*32, "b"*64, "r", "s", 0, "user", "界" * 5000, None, "public")
        with self.assertRaises(InvalidExtraction):
            p.extract([source], Snapshot(0, 0, {}, {}, []))

    def test_malformed_extraction_items_fail_with_typed_error_and_no_partial_commit(self):
        cases = [{"nodes": [], "relations": ["bad item"], "governance": []},
                 {"nodes": [fact("Source", "x", valid_date=17)], "relations": [], "governance": []},
                 {"nodes": [fact("Source", "x")], "relations": [
                     {"message": 0, "quote": "Source", "from": [], "to": "f", "kind": "SUPPORTS"}], "governance": []},
                 {"nodes": [], "relations": [], "governance": [
                     {"message": 0, "quote": "Source", "action": "erase", "target_ids": 42}]}]
        for i, data in enumerate(cases):
            with self.subTest(i=i):
                self.provider.extract = lambda *args, value=data: value
                with self.assertRaises(InvalidExtraction):
                    self.pipeline.add(AddRequest(f"bad-{i}", "u", "s", [Message("user", "Source")]))
                self.assertEqual(self.inspect()["sources"], [])


for _name in list(vars(fixtures.LifecycleTests)):
    if _name.startswith("test_"):
        setattr(LongAddRegressionTests, _name, None)
