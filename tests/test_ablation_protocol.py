import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from ablate import FACTORS, IMPROVEMENTS, selector_controls, variants
from frozen_provider import FrozenPublicProvider
from test_lifecycle import AnnotatedProviders, fact
from eviepoch.config import Settings
from eviepoch.domain import InvalidExtraction, Node, Obligation, QueryPlan, Snapshot, Source, namespace, stable_id
from eviepoch.retrieval import Evidence


class AblationProtocolTests(unittest.TestCase):
    def test_improvement_removals_hold_other_features_and_budgets_fixed(self):
        config = Settings()
        for name, variant in variants(config, improvements=True).items():
            changes = [f for f in FACTORS + IMPROVEMENTS if getattr(config, f) != getattr(variant, f)]
            self.assertEqual(len(changes), 0 if name == "full" else 1)
            self.assertEqual(variant.answer_input_tokens, config.answer_input_tokens)
            self.assertEqual(variant.llm_model, config.llm_model)
            self.assertEqual(variant.temporal_anchors_enabled, config.temporal_anchors_enabled)

    def test_each_removal_changes_only_one_factor_not_model_or_budget(self):
        config = Settings()
        for name, variant in variants(config).items():
            changed = [f for f in FACTORS if getattr(config, f) != getattr(variant, f)]
            self.assertEqual(len(changed), 0 if name == "full" else 1)
            self.assertEqual((variant.llm_model, variant.embed_model, variant.answer_input_tokens),
                             (config.llm_model, config.embed_model, config.answer_input_tokens))

    def test_frozen_extraction_does_not_depend_on_summary_or_later_arm_context(self):
        actual = AnnotatedProviders()
        actual.config = Settings()
        actual.register("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
        provider = FrozenPublicProvider(actual)
        source = Source("a" * 32, "b" * 64, "r", "s", 0, "user", "My address is Kyoto.", None, "ignored")
        known = Snapshot(0, 0, {}, {}, [])
        original = provider.extract([source], known)
        provider.sealed = True
        actual.register("My address is Kyoto.", [fact("My address is Kyoto.", "WRONG")])
        known.nodes["summary"] = Node("summary", "summary", "user", "address", "unrelated", [], [])
        replay = provider.extract([replace(source, stored_at="different")], known)
        self.assertEqual(replay, original)
        replay["nodes"][0]["value"] = "mutated"
        self.assertEqual(provider.extract([source], known), original)
        with self.assertRaises(InvalidExtraction):
            provider.extract([replace(source, content="new input")], known)

    def test_selector_counterfactuals_have_identical_candidates_plan_and_budget(self):
        actual = AnnotatedProviders()
        provider = FrozenPublicProvider(actual)
        ns = namespace("public-control:case")
        eid = stable_id(ns, "r", 0, "source-v1")
        plan = QueryPlan(obligations=[Obligation("o", "address", alternatives=[[eid]])])
        provider.record_selection("address", plan, {eid: Evidence(eid, "My address is Kyoto.", None, 1, eid)}, 1000, Settings())
        data = {"cases": [{"id": "case", "searches": [{"id": "q", "query": "address", "top_k": 1,
                                                      "evidence_alternatives": [["r:0"]]}]}]}
        rows = [v["rows"][0] for v in selector_controls(data, provider, False).values()]
        self.assertEqual(len({r["candidate_hash"] for r in rows}), 1)
        self.assertEqual(len({r["plan_hash"] for r in rows}), 1)
        self.assertEqual({r["budget"] for r in rows}, {1000})


if __name__ == "__main__":
    unittest.main()
