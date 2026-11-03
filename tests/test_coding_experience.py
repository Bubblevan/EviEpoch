"""Short experience polarity (TRD §19.3)."""
from __future__ import annotations

import unittest

from eviepoch.adapters.coding.experience import claims_fix
from eviepoch.adapters.coding.ingest import ingest_sources
from eviepoch.adapters.coding.search import applicability_verdict
from eviepoch.domain import Source, stable_id, timestamp

REMOTE = "git@example.com:org/service.git"
COMMIT = "a" * 40
OTHER = "b" * 40
TREE = "c" * 40
DIFF = (
    "diff --git a/pkg/handler.py b/pkg/handler.py\n"
    "--- a/pkg/handler.py\n"
    "+++ b/pkg/handler.py\n"
    "@@ -1 +1 @@\n"
    "-raise NullPointerError\n"
    "+return safe_default()\n"
)


def _source(content: str, ordinal: int) -> Source:
    return Source(
        stable_id("ns", "experience", ordinal, content[:24]),
        "ns",
        "experience",
        "session",
        ordinal,
        "assistant",
        content,
        timestamp(1700000000000),
        "2024-01-01T00:00:00Z",
    )


def _experiences(extension) -> list[dict]:
    return [item.payload for item in extension.artifacts if item.kind == "coding_experience"]


class ExperiencePolarityTests(unittest.TestCase):
    def test_fixed_without_a_passing_run_is_only_a_claim(self) -> None:
        content = f"repository: {REMOTE}\ncommit: {COMMIT}\nfixed the null crash\n{DIFF}"
        extension = ingest_sources([_source(content, 1)])
        experience = _experiences(extension)[0]
        self.assertEqual(experience["polarity"], "unknown")
        self.assertEqual(experience["state"], "proposed")
        self.assertEqual(experience["verification"], "claimed")
        self.assertEqual(experience["causal"], "stated")
        self.assertEqual(experience["applicability"], "unknown")
        self.assertEqual(experience["observed_result"], "")
        self.assertTrue(experience["attempted_change"])
        self.assertFalse(any(edge.kind == "CAUSED" for edge in extension.edges))

    def test_fixed_inside_a_diff_is_not_a_claim(self) -> None:
        content = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\n"
            "diff --git a/pkg/handler.py b/pkg/handler.py\n"
            "--- a/pkg/handler.py\n+++ b/pkg/handler.py\n@@ -1 +1 @@\n-old\n+fixed\n"
        )
        self.assertFalse(claims_fix(content, content[content.find("diff --git "):]))
        experience = _experiences(ingest_sources([_source(content, 2)]))[0]
        self.assertEqual(experience["verification"], "none")
        self.assertEqual(experience["polarity"], "unknown")
        self.assertNotEqual(experience["state"], "tested")

    def test_same_source_failure_overrides_the_word_fixed(self) -> None:
        content = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\nfixed the null crash\n{DIFF}"
            "pytest pkg/test.py\nFAILED pkg/test.py::test_null\nexit code: 1\n"
        )
        extension = ingest_sources([_source(content, 3)])
        experience = _experiences(extension)[0]
        self.assertEqual(experience["polarity"], "failure")
        self.assertEqual(experience["state"], "observed")
        self.assertEqual(experience["verification"], "none")
        self.assertEqual(experience["causal"], "experimentally_supported")
        self.assertIn("FAILED pkg/test.py::test_null", experience["failure_symptoms"])
        self.assertTrue(any(edge.kind == "TESTED_ON" for edge in extension.edges))
        self.assertFalse(any(edge.kind == "VERIFIED_BY" for edge in extension.edges))

    def test_exit_zero_on_the_same_snapshot_verifies_the_claim(self) -> None:
        claim = f"repository: {REMOTE}\ncommit: {COMMIT}\ntree: {TREE}\nfixed the null crash\n{DIFF}"
        passed = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\ntree: {TREE}\n"
            "pytest pkg/test.py\nPASSED\nexit code: 0\n"
        )
        extension = ingest_sources([_source(claim, 4), _source(passed, 5)])
        rows = _experiences(extension)
        claim_row = next(item for item in rows if item["attempted_change"])
        run_row = next(item for item in rows if not item["attempted_change"])
        self.assertEqual(claim_row["polarity"], "success")
        self.assertEqual(claim_row["state"], "tested")
        self.assertEqual(claim_row["verification"], "verified")
        self.assertEqual(claim_row["causal"], "experimentally_supported")
        self.assertEqual(claim_row["applicability"], "unknown")
        self.assertEqual(run_row["polarity"], "success")
        self.assertNotEqual(claim_row["experience_id"], run_row["experience_id"])
        test_run = next(item.payload for item in extension.artifacts if item.kind == "coding_test_run")
        self.assertEqual(test_run["tree_sha"], TREE)
        self.assertEqual(test_run["exit_code"], 0)
        self.assertEqual(test_run["outcome"], "success")
        self.assertIn(test_run["test_run_id"], claim_row["test_run_ids"])
        self.assertTrue(any(edge.kind == "VERIFIED_BY" and edge.origin == test_run["test_run_id"] for edge in extension.edges))

    def test_a_pass_on_another_snapshot_does_not_verify_the_claim(self) -> None:
        claim = f"repository: {REMOTE}\ncommit: {COMMIT}\nfixed the null crash\n{DIFF}"
        passed = f"repository: {REMOTE}\ncommit: {OTHER}\npytest pkg/test.py\nPASSED\nexit code: 0\n"
        extension = ingest_sources([_source(claim, 6), _source(passed, 7)])
        rows = _experiences(extension)
        claim_row = next(item for item in rows if item["attempted_change"])
        run_row = next(item for item in rows if item["polarity"] == "success")
        self.assertEqual(claim_row["polarity"], "unknown")
        self.assertEqual(claim_row["verification"], "claimed")
        self.assertEqual(claim_row["state"], "proposed")
        self.assertEqual(claim_row["applicability"], "unknown")
        self.assertNotEqual(claim_row["snapshot_id"], run_row["snapshot_id"])
        self.assertNotEqual(claim_row["experience_id"], run_row["experience_id"])

    def test_a_failure_on_the_same_snapshot_contradicts_the_claim(self) -> None:
        claim = f"repository: {REMOTE}\ncommit: {COMMIT}\nfixed the null crash\n{DIFF}"
        failed = f"repository: {REMOTE}\ncommit: {COMMIT}\npytest pkg/test.py\nFAILED pkg/test.py::test_null\nexit code: 1\n"
        rows = _experiences(ingest_sources([_source(claim, 8), _source(failed, 9)]))
        claim_row = next(item for item in rows if item["attempted_change"])
        failure = next(item for item in rows if item["polarity"] == "failure")
        self.assertEqual(claim_row["polarity"], "unknown")
        self.assertEqual(claim_row["state"], "contradicted")
        self.assertNotEqual(claim_row["polarity"], "success")
        self.assertIn(failure["test_run_ids"][0], claim_row["counterexample_ids"])
        self.assertEqual(failure["occurrence"], "independent")

    def test_independent_failures_stay_separate_and_duplicate_logs_are_marked(self) -> None:
        def failed(call: str | None) -> str:
            line = f"tool_call_id: {call}\n" if call else ""
            return (
                f"repository: {REMOTE}\ncommit: {COMMIT}\n{line}"
                "pytest pkg/test.py\nFAILED pkg/test.py::test_null\nexit code: 1\n"
            )

        separate = _experiences(ingest_sources([_source(failed("run-1"), 10), _source(failed("run-2"), 11)]))
        self.assertEqual(len(separate), 2)
        self.assertNotEqual(separate[0]["experience_id"], separate[1]["experience_id"])
        self.assertTrue(all(item["polarity"] == "failure" and item["occurrence"] == "independent" for item in separate))
        self.assertEqual(separate[0]["observed_result"].strip(), separate[1]["observed_result"].strip())

        repeated = failed(None)
        duplicates = _experiences(ingest_sources([_source(repeated, 12), _source(repeated, 13)]))
        self.assertEqual(len(duplicates), 2)
        self.assertEqual([item["occurrence"] for item in duplicates], ["independent", "duplicate_log"])
        self.assertTrue(all(item["polarity"] == "failure" for item in duplicates))
        self.assertNotEqual(duplicates[0]["experience_id"], duplicates[1]["experience_id"])

    def test_mixed_output_is_not_success(self) -> None:
        content = f"repository: {REMOTE}\ncommit: {COMMIT}\npytest pkg/test.py\nPASSED\nFAILED pkg/test.py::test_null\nexit code: 0\n"
        experience = _experiences(ingest_sources([_source(content, 14)]))[0]
        self.assertEqual(experience["polarity"], "mixed")
        self.assertNotEqual(experience["verification"], "verified")
        self.assertNotEqual(experience["state"], "tested")

    def test_another_environment_does_not_make_the_claim_effective(self) -> None:
        claim = f"repository: {REMOTE}\ncwd: /work/a\ncommit: {COMMIT}\nfixed the null crash\n{DIFF}"
        passed = f"repository: {REMOTE}\ncwd: /work/b\ncommit: {COMMIT}\npytest pkg/test.py\nPASSED\nexit code: 0\n"
        rows = _experiences(ingest_sources([_source(claim, 15), _source(passed, 16)]))
        claim_row = next(item for item in rows if item["environment"] == "/work/a")
        self.assertEqual(claim_row["polarity"], "unknown")
        self.assertEqual(claim_row["verification"], "claimed")
        self.assertFalse(claim_row["applies_as_current_solution"])

    def test_stated_reason_rule_and_reuse_limit_stay_on_the_experience(self) -> None:
        content = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\n"
            "reason: null input used to crash the handler\n"
            "rule: return a default for null\n"
            "do not reuse: callee starts rejecting null\n"
            "applicability: unknown\n"
            f"fixed the null crash\n{DIFF}"
            "pytest pkg/test.py\nPASSED\nexit code: 0\n"
        )
        extension = ingest_sources([_source(content, 17)])
        experience = _experiences(extension)[0]
        self.assertEqual(experience["reason"], "null input used to crash the handler")
        self.assertEqual(experience["rule"], "return a default for null")
        self.assertEqual(experience["reuse_condition"], "callee starts rejecting null")
        self.assertIn("add return safe_default()", experience["attempted_change"])
        self.assertEqual(experience["verification"], "verified")
        self.assertEqual(experience["applicability"], "unknown")
        self.assertFalse(experience["applies_as_current_solution"])
        self.assertTrue(any(edge.kind == "EXCEPT_IF" for edge in extension.edges))

    def test_explicit_compatible_scope_can_be_a_current_solution(self) -> None:
        content = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\napplicability: compatible\n"
            f"fixed the null crash\n{DIFF}pytest pkg/test.py\nPASSED\nexit code: 0\n"
        )
        experience = _experiences(ingest_sources([_source(content, 18)]))[0]
        self.assertTrue(experience["applies_as_current_solution"])

    def test_a_new_source_can_supersede_an_earlier_attempt(self) -> None:
        earlier = f"repository: {REMOTE}\ncommit: {COMMIT}\ntool_call_id: old-fix\nfixed the null crash\n{DIFF}"
        later = f"repository: {REMOTE}\ncommit: {COMMIT}\ntool_call_id: new-fix\nsupersedes: old-fix\n{DIFF}"
        rows = _experiences(ingest_sources([_source(earlier, 19), _source(later, 20)]))
        old = next(item for item in rows if "old-fix" in item["experience_id"] or item["verification"] == "claimed")
        self.assertEqual(old["state"], "superseded")
        self.assertFalse(old["applies_as_current_solution"])

    def test_decision_outcome_keeps_stated_premises_symptoms_and_support(self) -> None:
        content = (
            f"repository: {REMOTE}\ncommit: {COMMIT}\n"
            "decided to return a default\n"
            "premise: null input is possible\n"
            "alternative: raise immediately\n"
            "pytest pkg/test.py\nFAILED pkg/test.py::test_null\nexit code: 1\n"
        )
        extension = ingest_sources([_source(content, 21)])
        outcome = next(item.payload for item in extension.artifacts if item.kind == "coding_decision_outcome")
        self.assertEqual(outcome["premises"], ["null input is possible"])
        self.assertEqual(outcome["alternatives"], ["raise immediately"])
        self.assertIn("FAILED pkg/test.py::test_null", outcome["failure_symptoms"])
        self.assertTrue(outcome["evidence_span_ids"])
        self.assertTrue(outcome["counterexample_ids"])
        self.assertEqual(outcome["support_ids"], [])
        self.assertEqual(outcome["polarity"], "failure")

    def test_failed_history_is_not_a_compatible_current_solution(self) -> None:
        verdict, _reasons, _gaps = applicability_verdict({"outcome": "failure", "exit_code": 1, "raw_diff": "diff"}, None)
        self.assertNotEqual(verdict, "compatible")
        self.assertEqual(verdict, "unknown")
        unknown, _reasons, _gaps = applicability_verdict({"applicability": "unknown", "polarity": "success", "exit_code": 0}, None)
        self.assertEqual(unknown, "unknown")
        incompatible, _reasons, _gaps = applicability_verdict({"applicability": "incompatible", "polarity": "success", "exit_code": 0}, None)
        self.assertEqual(incompatible, "incompatible")


if __name__ == "__main__":
    unittest.main()
