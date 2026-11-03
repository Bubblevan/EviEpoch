from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "experiments" / "coding" / "baselines" / "coding_baseline_lock.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CodingBaselineLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    def test_vendored_claude_mem_files_match_v15_8_3(self) -> None:
        claude = self.lock["claude_mem"]
        self.assertEqual(claude["tag"], "v15.8.3")
        self.assertEqual(claude["commit"], "9603d9bd7ba2bf1ed53595cf7eaa7e7b6a8013e4")
        self.assertEqual(claude["license"], "Apache-2.0")
        for item in claude["files"].values():
            self.assertEqual(_sha256(ROOT / item["vendored"]), item["sha256"])
        self.assertEqual(_sha256(ROOT / claude["notice"]), claude["license_sha256"])

    def test_observation_contract_matches_code_json_and_parser(self) -> None:
        from eviepoch.adapters.coding.parse_agent_xml import (
            MAX_PARSED_ATTACHMENTS,
            MAX_PARSED_ATTACHMENT_CHARS,
            OBSERVATION_SCHEMA_TAGS,
            OBSERVATION_TITLE_MAX_GRAPHEMES,
            OBSERVATION_TITLE_TRUNCATE_AT,
            parse_observations,
            valid_observation_types,
        )

        claude = self.lock["claude_mem"]
        code = json.loads((ROOT / claude["files"]["plugin/modes/code.json"]["vendored"]).read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in code["observation_types"]], claude["observation_types"])
        self.assertEqual([item["id"] for item in code["observation_concepts"]], claude["observation_concepts"])
        self.assertEqual(valid_observation_types(), claude["observation_types"])
        parser = claude["parser"]
        self.assertEqual(OBSERVATION_TITLE_MAX_GRAPHEMES, parser["title_max_graphemes"])
        self.assertEqual(OBSERVATION_TITLE_TRUNCATE_AT, parser["title_truncate_at"])
        self.assertEqual(MAX_PARSED_ATTACHMENTS, parser["max_parsed_attachments"])
        self.assertEqual(MAX_PARSED_ATTACHMENT_CHARS, parser["max_parsed_attachment_chars"])
        self.assertEqual(sorted(OBSERVATION_SCHEMA_TAGS), sorted(parser["schema_tags"]))
        parsed = parse_observations(
            "<observation><type>not-a-real-type</type><title>Keep</title>"
            "<facts><fact>one</fact></facts></observation>"
        )
        self.assertEqual(parsed[0].type, "not-a-real-type")
        self.assertEqual(parsed[0].facts, ["one"])

    def test_timeline_depth_deviation_is_recorded(self) -> None:
        from eviepoch.adapters.coding.timeline import TimelineItem, filter_by_depth

        timeline = self.lock["claude_mem"]["timeline"]
        self.assertEqual(timeline["search_manager_parse_depth_default"], 10)
        deviation = self.lock["claude_mem"]["evi_epoch_deviations"][0]
        self.assertIn("depth_before=3", deviation["local"])
        self.assertIn("depth_after=5", deviation["local"])
        items = [
            TimelineItem("observation", {"id": index}, index)
            for index in (1, 2, 3)
        ]
        window = filter_by_depth(items, 2, 2, 1, 0)
        self.assertEqual([item.data["id"] for item in window], [1, 2])

    def test_causal_entry_fields_are_locked(self) -> None:
        fields = self.lock["causal_memory"]["fields"]
        self.assertEqual(fields[1:6], ["decision_id", "decision_text", "outcome_id", "outcome_text", "relation"])
        self.assertIn("outcome_polarity", fields)
        self.assertIn("superseded_by", fields)
        self.assertIn("context_fingerprint", fields)
        self.assertIn("influenced_by", fields)
        self.assertEqual(self.lock["causal_memory"]["commit"], "39053ff6fd7a18771d59f0c10240b19c37f412b6")

    def test_public_datasets_are_pinned_but_not_downloaded(self) -> None:
        datasets = self.lock["datasets"]
        self.assertFalse(datasets["downloaded"])
        swe = datasets["swe_context_bench"]
        self.assertEqual(swe["commit"], "12ad6ab14e18e9378e1e293c9edbc3f7ce43d27b")
        self.assertEqual(swe["readme_counts"]["base_tasks"], 1007)
        self.assertEqual(swe["readme_counts"]["related_tasks"], 362)
        self.assertIsNone(swe["huggingface_revision"])
        memdev = datasets["memdev_bench"]
        self.assertEqual(memdev["paper"], "https://arxiv.org/abs/2610.27524v1")
        self.assertEqual(memdev["abstract_counts"]["base_tasks"], 555)
        self.assertEqual(memdev["abstract_counts"]["historical_tasks"], 4869)
        self.assertIsNone(memdev["code_repository"])
        raw = LOCK_PATH.read_text(encoding="utf-8")
        self.assertNotIn("62.00", raw)
        self.assertNotIn("52.00", raw)
        self.assertNotIn("52.67", raw)

    def test_fault_fixture_command_covers_section_22_4(self) -> None:
        script = ROOT / self.lock["fixture"]["builder"]
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"ok": true', completed.stdout)
        self.assertEqual(
            self.lock["fixture"]["cases"],
            [
                "same_path_different_repo",
                "fork_merge_rollback",
                "dirty_overlay",
                "symbol_rename",
                "removed_api",
                "fail_then_success",
                "test_passed_on_wrong_tree",
                "missing_verification",
                "diff_source_round_trip",
            ],
        )


if __name__ == "__main__":
    unittest.main()
