"""Typed evidence-set selection (K5 / E4 coding slice)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.coding.search import evidence_roles, required_evidence_roles, select_typed_evidence
from eviepoch.config import Settings
from eviepoch.domain import QueryPlan
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from eviepoch.retrieval.evidence import Evidence
from tests import test_lifecycle as fixtures

ORG = "git@example.com:org/service.git"
COMMIT = "a" * 40
PARENT = "c" * 40
FORK = "b" * 40
OWN = f"ancestry {PARENT}\nancestry {COMMIT} {PARENT}\n"
FORK_GRAPH = f"{OWN}ancestry {FORK} {PARENT}\n"
DIFF = (
    "diff --git a/pkg/handler.py b/pkg/handler.py\n"
    "--- a/pkg/handler.py\n"
    "+++ b/pkg/handler.py\n"
    "@@ -1 +1 @@\n"
    "-raise NullPointerError\n"
    "+return safe_default_TOKEN()\n"
)


def _settings(**overrides) -> Settings:
    values = dict(
        task_adapter="coding",
        llm_api_key="test",
        embed_api_key="test",
        embed_base_url="https://example.test/v1",
        embed_dimensions=32,
        auth_scheme="none",
        rerank_enabled=False,
        window_radius=0,
    )
    values.update(overrides)
    return Settings(**values)


class TypedEvidenceSetTests(unittest.TestCase):
    def test_roles_follow_the_task_and_the_source_text(self) -> None:
        self.assertEqual(required_evidence_roles(QueryPlan(task_kind="bugfix")), ["reproduction", "cause", "patch", "test"])
        self.assertEqual(required_evidence_roles(QueryPlan(task_kind="feature")), ["requirements", "constraints", "dependencies", "test"])
        self.assertEqual(required_evidence_roles(QueryPlan()), [])
        self.assertIn("reproduction", evidence_roles("FAILED pkg/handler.py\nexit code: 1"))
        self.assertNotIn("failed_attempt", evidence_roles("FAILED pkg/handler.py\nexit code: 1"))
        self.assertIn("failed_attempt", evidence_roles("diff --git a/a b/a\nFAILED\nexit code: 1"))
        self.assertIn("patch", evidence_roles(DIFF))
        self.assertNotIn("patch", evidence_roles("diff --git a/a b/a\nFAILED\nexit code: 1"))
        self.assertIn("failed_attempt", evidence_roles("diff --git a/a b/a\nFAILED\nexit code: 1"))
        self.assertIn("test", evidence_roles("PASSED\nexit code: 0"))

    def test_same_candidates_topk_drops_the_premise_typed_beam_keeps(self) -> None:
        config = _settings()
        plan = QueryPlan(task_kind="bugfix")
        items = [
            Evidence("noise", "pkg/handler.py NOISE_TOKEN status", None, 10, "noise"),
            Evidence("repro", "FAILED pkg/handler.py\nREPRO_TOKEN\nexit code: 1", None, 1, "repro"),
            Evidence("cause", "root cause: null dereference\npremise: input can be null\nCAUSE_TOKEN", None, 1, "cause"),
            Evidence("patch", DIFF, None, 3, "patch"),
            Evidence("test", "pytest pkg/handler.py\nPASSED\nexit code: 0\nTEST_TOKEN", None, 2, "test"),
            Evidence("fork", "[applicability:incompatible] version relation: fork\nFORK_TOKEN", None, 0.2, "fork"),
        ]
        chosen, report = select_typed_evidence(None, items, [], plan, config, 1, {}, "bug handler")
        self.assertEqual(report["candidates"], [item.id for item in items])
        self.assertTrue(set(report["topk"]["ids"]) <= set(report["candidates"]))
        self.assertTrue(set(report["greedy"]["ids"]) <= set(report["candidates"]))
        self.assertTrue(set(report["typed_beam"]["ids"]) <= set(report["candidates"]))
        self.assertFalse(set(report["topk"]["covered"]) >= set(required_evidence_roles(plan)))
        self.assertTrue(set(report["typed_beam"]["covered"]) >= set(required_evidence_roles(plan)))
        body = "\n".join(item.content for item in chosen)
        self.assertLess(body.index("REPRO_TOKEN"), body.index("CAUSE_TOKEN"))
        self.assertLess(body.index("CAUSE_TOKEN"), body.index("return safe_default_TOKEN()"))
        self.assertLess(body.index("return safe_default_TOKEN()"), body.index("TEST_TOKEN"))
        self.assertIn(DIFF, body)
        self.assertNotIn("NOISE_TOKEN", body)
        self.assertIn("FORK_TOKEN", body)
        self.assertEqual(report["completeness"]["gaps"], [])
        self.assertTrue(report["typed_beam"]["alternatives"])
        self.assertEqual(len(chosen), 1)

    def test_a_missing_role_stays_a_gap_and_a_tight_budget_is_recorded(self) -> None:
        config = _settings()
        plan = QueryPlan(task_kind="bugfix")
        items = [
            Evidence("repro", "FAILED pkg/handler.py\nREPRO_TOKEN\nexit code: 1", None, 1, "repro"),
            Evidence("patch", DIFF, None, 2, "patch"),
            Evidence("test", "PASSED\nexit code: 0\nTEST_TOKEN", None, 1, "test"),
        ]
        chosen, report = select_typed_evidence(None, items, [], plan, config, 4, {}, "bug handler")
        self.assertIn("REPRO_TOKEN", chosen[0].content)
        self.assertNotIn("CAUSE_TOKEN", chosen[0].content)
        self.assertIn({"role": "cause", "action": "absent"}, report["completeness"]["gaps"])
        tight = _settings(char_budget=90)
        _chosen, tight_report = select_typed_evidence(None, items, [], plan, tight, 4, {}, "bug handler")
        self.assertTrue(all(len(item.content) <= 90 for item in _chosen))
        self.assertTrue(any(gap["action"] == "budget" for gap in tight_report["completeness"]["gaps"]))

    def test_search_packs_a_bugfix_and_records_an_absent_cause(self) -> None:
        self._search_case(include_cause=True)
        self._search_case(include_cause=False)

    def _search_case(self, include_cause: bool) -> None:
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        provider = fixtures.AnnotatedProviders()
        pipeline = MemoryPipeline(_settings(db_path=str(Path(temp.name) / "set.sqlite3")), provider)
        pipeline.start()
        try:
            messages = [
                f"repository: {ORG}\ncommit: {COMMIT}\n{OWN}path: pkg/handler.py\nNOISE_TOKEN status\n",
                f"repository: {ORG}\ncommit: {COMMIT}\n{OWN}FAILED pkg/handler.py::test_null\nREPRO_TOKEN\nexit code: 1\n",
                f"repository: {ORG}\ncommit: {COMMIT}\n{OWN}{DIFF}",
                f"repository: {ORG}\ncommit: {COMMIT}\n{OWN}pytest pkg/handler.py\nPASSED\nexit code: 0\nTEST_TOKEN\n",
                f"repository: {ORG}\ncommit: {FORK}\n{FORK_GRAPH}path: pkg/handler.py\nFORK_TOKEN\n",
            ]
            if include_cause:
                messages.append(
                    f"repository: {ORG}\ncommit: {COMMIT}\n{OWN}root cause: null dereference\npremise: input can be null\nCAUSE_TOKEN\n"
                )
            for index, content in enumerate(messages):
                provider.register(content, nodes=())
                pipeline.add(AddRequest(f"set-{include_cause}-{index}", "dev-a", "sess-set", [Message("tool", content, 1700000000000 + index)]))
            provider.plan_value = {"intents": ["fact"], "obligations": [{"query": "bug handler"}]}
            query = f"bug repository: {ORG} commit: {COMMIT} path: pkg/handler.py"
            response, trace = pipeline.search_with_trace(SearchRequest(query=query, user_id="dev-a", top_k=1))
            report = trace["typed_selection"]
            body = "\n".join(item["content"] for item in response["data"])
            self.assertIn(DIFF, body)
            self.assertIn("REPRO_TOKEN", body)
            self.assertIn("TEST_TOKEN", body)
            self.assertIn("FORK_TOKEN", body)
            self.assertNotIn("NOISE_TOKEN", body)
            self.assertEqual(report["chosen"], "typed_beam")
            self.assertTrue(set(report["topk"]["ids"]) <= set(report["candidates"]))
            self.assertTrue(set(report["typed_beam"]["ids"]) <= set(report["candidates"]))
            self.assertFalse(set(report["topk"]["covered"]) >= {"reproduction", "cause", "patch", "test"})
            if include_cause:
                self.assertIn("CAUSE_TOKEN", body)
                self.assertLess(body.index("REPRO_TOKEN"), body.index("CAUSE_TOKEN"))
                self.assertLess(body.index("CAUSE_TOKEN"), body.index("return safe_default_TOKEN()"))
                self.assertTrue({"reproduction", "cause", "patch", "test"} <= set(report["typed_beam"]["covered"]))
                self.assertEqual(report["completeness"]["gaps"], [])
            else:
                self.assertNotIn("CAUSE_TOKEN", body)
                self.assertIn({"role": "cause", "action": "absent"}, report["completeness"]["gaps"])
        finally:
            pipeline.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
