"""Version-applicability search (TRD §19.5)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.coding.search import applicability_verdict, plan_obligations, query_version, version_channels, version_file_gaps
from eviepoch.domain import Obligation, QueryPlan
from eviepoch.config import Settings
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from tests import test_lifecycle as fixtures

ORG = "git@example.com:org/service.git"
OTHER = "git@example.com:other/service.git"
C1 = "1" * 40
C2 = "2" * 40
C5 = "5" * 40
C6 = "6" * 40
ANCESTOR_TOKEN = "ANCESTOR_TOKEN_v5"
FORK_TOKEN = "FORK_TOKEN_v5"
OTHER_REPO_TOKEN = "OTHER_REPO_TOKEN_v5"
SUCCESS_TOKEN = "SUCCESS_TOKEN_v5"
FAIL_TOKEN = "FAIL_TOKEN_v5"


def _verdict(payload: dict, query: str, historical: str, **kwargs):
    return applicability_verdict(payload, query, historical_sha=historical, **kwargs)


class VersionApplicabilityTests(unittest.TestCase):
    def test_only_explicit_version_fields_enter_the_filter(self) -> None:
        bare = query_version("pkg/handler.py handler")
        self.assertIsNone(bare["commit"])
        self.assertIsNone(bare["path"])
        self.assertIsNone(bare["repository"])
        named = query_version(f"repository: {ORG} commit: {C5} path: pkg/handler.py symbol: Box.open")
        self.assertEqual(named["commit"], C5)
        self.assertEqual(named["repository"], ORG.casefold())
        self.assertEqual(named["path"], "pkg/handler.py")
        self.assertEqual(named["symbol"], "Box.open")
        self.assertFalse(named["analogy"])
        self.assertTrue(query_version("allow analogy across this repository: " + ORG)["analogy"])
        self.assertEqual(version_channels(object(), "pkg/handler.py", 10), ({}, {}, {}))
        plan = plan_obligations(
            QueryPlan(["fact"], obligations=[Obligation("o1", "handler")]),
            f"bug repository: {ORG} commit: {C5} symbol: Box.open acceptance: tests pass",
        )
        self.assertEqual(plan.task_kind, "bugfix")
        self.assertEqual(plan.target_repository, ORG.casefold())
        self.assertEqual(plan.target_snapshot, C5)
        self.assertEqual(plan.affected_symbols, ["Box.open"])
        self.assertEqual(plan.acceptance, ["tests pass"])
        self.assertTrue(any(item.query.startswith("implementation_constraints:") for item in plan.obligations))

    def test_same_snapshot_success_is_the_only_current_patch(self) -> None:
        payload = {"exit_code": 0, "outcome": "success"}
        current, _reasons, _gaps = _verdict(payload, C5, C5, query_repo=ORG, historical_repo=ORG)
        self.assertEqual(current, "compatible")
        ancestor, reasons, _gaps = _verdict(
            payload, C5, C2, parents={C5: (C2,), C2: (C1,)}, query_repo=ORG, historical_repo=ORG
        )
        self.assertEqual(ancestor, "unknown")
        self.assertIn("version relation: descendant", reasons)
        fork, _reasons, _gaps = _verdict(
            payload, C5, C6, parents={C5: (C2,), C6: (C2,), C2: (C1,)}, query_repo=ORG, historical_repo=ORG
        )
        self.assertEqual(fork, "incompatible")
        rollback, _reasons, _gaps = _verdict(
            payload, C5, C2, parents={C5: (C2,)}, reverts={C5: C2}, query_repo=ORG, historical_repo=ORG
        )
        self.assertEqual(rollback, "incompatible")
        other, _reasons, _gaps = _verdict(payload, C5, C5, query_repo=ORG, historical_repo=OTHER)
        self.assertEqual(other, "incompatible")
        dirty, _reasons, _gaps = _verdict(
            {"exit_code": 0, "outcome": "success", "dirty_overlay_hash": "abc"},
            C5,
            C5,
            query_repo=ORG,
            historical_repo=ORG,
        )
        self.assertEqual(dirty, "unknown")
        unnamed, _reasons, _gaps = _verdict(payload, C5, C5, historical_repo=ORG)
        self.assertEqual(unnamed, "unknown")
        failed, reasons, _gaps = _verdict(
            {"exit_code": 1, "outcome": "failure"}, C5, C5, query_repo=ORG, historical_repo=ORG
        )
        self.assertEqual(failed, "unknown")
        self.assertTrue(any("failed attempt" in reason for reason in reasons))

    def test_file_or_unverified_symbol_change_blocks_a_current_patch(self) -> None:
        gaps = version_file_gaps({"pkg/handler.py": "aaa"}, {"pkg/handler.py": "bbb"}, {}, {})
        self.assertTrue(any("changed" in gap for gap in gaps))
        verdict, _reasons, _gaps = _verdict(
            {"exit_code": 0, "outcome": "success"},
            C5,
            C5,
            query_repo=ORG,
            historical_repo=ORG,
            file_gaps=gaps,
        )
        self.assertEqual(verdict, "unknown")
        renamed = version_file_gaps({}, {}, {"Box.open": {"rename_status": "verified"}}, {"Box.open": {"rename_status": "unverified"}})
        self.assertTrue(any("unverified" in gap for gap in renamed))

    def test_search_uses_stored_ancestry_and_keeps_conflicts_visible(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        provider = fixtures.AnnotatedProviders()
        config = Settings(
            task_adapter="coding",
            db_path=str(Path(temp.name) / "version.sqlite3"),
            llm_api_key="test",
            embed_api_key="test",
            embed_base_url="https://example.test/v1",
            embed_dimensions=32,
            auth_scheme="none",
            rerank_enabled=False,
            window_radius=0,
        )
        pipeline = MemoryPipeline(config, provider)
        pipeline.start()
        try:
            messages = [
                (
                    "ancestor",
                    1577836800000,
                    f"repository: {ORG}\ncommit: {C2}\nancestry {C1}\nancestry {C2} {C1}\n"
                    f"{ANCESTOR_TOKEN}\npkg/handler.py historical fix\n",
                ),
                (
                    "success",
                    1700000000000,
                    f"repository: {ORG}\ncommit: {C5}\nancestry {C5} {C2}\npath: pkg/handler.py\n"
                    f"{SUCCESS_TOKEN}\npytest pkg/handler.py\nPASSED\nexit code: 0\n",
                ),
                (
                    "failure",
                    1700000001000,
                    f"repository: {ORG}\ncommit: {C5}\npath: pkg/handler.py\n{FAIL_TOKEN}\n"
                    "pytest pkg/handler.py\nFAILED pkg/handler.py::test_other\nexit code: 1\n",
                ),
                (
                    "fork",
                    1700000002000,
                    f"repository: {ORG}\ncommit: {C6}\nancestry {C6} {C2}\npath: pkg/handler.py\n{FORK_TOKEN}\n",
                ),
                (
                    "other",
                    1700000003000,
                    f"repository: {OTHER}\ncommit: {C2}\npath: pkg/handler.py\n{OTHER_REPO_TOKEN}\n",
                ),
            ]
            for name, when, content in messages:
                provider.register(content, nodes=())
                pipeline.add(
                    AddRequest(
                        request_id=f"version-{name}",
                        user_id="dev-a",
                        session_id="sess-version",
                        messages=[Message("tool", content, when)],
                    )
                )
            provider.plan_value = {"intents": ["fact"], "obligations": [{"query": "handler"}]}
            query = f"repository: {ORG} commit: {C5} path: pkg/handler.py"
            response, trace = pipeline.search_with_trace(SearchRequest(user_id="dev-a", query=query, top_k=8))
            joined = {item["content"] for item in response["data"]}
            text = "\n".join(joined)
            self.assertIn(ANCESTOR_TOKEN, text)
            ancestor = next(item for item in joined if ANCESTOR_TOKEN in item)
            self.assertIn("[not a current solution]", ancestor)
            self.assertIn("version relation: descendant", ancestor)
            self.assertIn(FORK_TOKEN, text)
            fork = next(item for item in joined if FORK_TOKEN in item)
            self.assertIn("[applicability:incompatible]", fork)
            self.assertNotIn(OTHER_REPO_TOKEN, text)
            success = next(item for item in joined if SUCCESS_TOKEN in item)
            self.assertNotIn("[not a current solution]", success)
            failure = next(item for item in joined if FAIL_TOKEN in item)
            self.assertIn("[avoid repeating]", failure)
            self.assertIn("[not a current solution]", failure)
            relations = trace["version_relations"]
            self.assertIn("descendant", relations.values())
            self.assertIn("fork", relations.values())
            self.assertFalse(trace["admissible"]["collapsed_conflict"])
            self.assertTrue(trace["admissible"]["unresolved_conflict"])
            verdicts = {item["verdict"] for item in trace["coding_applicability"]}
            self.assertIn("compatible", verdicts)
            self.assertIn("incompatible", verdicts)
            self.assertEqual(trace["hypothesis"]["H"]["target_snapshot"], C5)
            self.assertTrue(trace["hypothesis"]["H"]["applicable_attempts"])
            self.assertTrue(trace["hypothesis"]["S"]["test_run_spans"])
            self.assertIn("search_index", trace["controller"])
            self.assertIn("hydrate_exact_evidence", trace["controller"])

            analogy_query = query + " cross-repo"
            analogy, _trace = pipeline.search_with_trace(SearchRequest(user_id="dev-a", query=analogy_query, top_k=8))
            analogy_text = "\n".join(item["content"] for item in analogy["data"])
            self.assertIn(OTHER_REPO_TOKEN, analogy_text)
            other = next(item["content"] for item in analogy["data"] if OTHER_REPO_TOKEN in item["content"])
            self.assertIn("[analogy]", other)
            self.assertIn("[applicability:incompatible]", other)
        finally:
            pipeline.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
