"""The multimodal baseline lock names commits and a local fault fixture, not a score."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "experiments" / "multimodal" / "baselines" / "multimodal_baseline_lock.json"


class MultimodalBaselineLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    def test_upstream_commits_are_pinned_and_datasets_stay_offline(self) -> None:
        self.assertFalse(self.lock["datasets"]["downloaded"])
        self.assertEqual(self.lock["atm_bench"]["commit"], "ef4e5dff1a47ec71213a06e359f02753defa8fb1")
        self.assertFalse(self.lock["atm_bench"]["dataset_downloaded"])
        self.assertEqual(self.lock["mem_gallery"]["commit"], "a93959e1e978a6a7d77798ae92c2ffe41c538c62")
        self.assertFalse(self.lock["mem_gallery"]["dataset_downloaded"])
        memeye = self.lock["memeye"]
        self.assertEqual(memeye["commit"], "0358e70d714980980dfd8c87903384db474f0b16")
        self.assertEqual(memeye["huggingface"], "MemEyeBench/MemEye")
        self.assertEqual(memeye["huggingface_revision"], "f139f89d08bf0f66986153b5ff010cac24ba383d")
        self.assertFalse(memeye["dataset_downloaded"])
        self.assertEqual(memeye["readme_counts"]["mirrored_questions"], 371)
        self.assertEqual(memeye["readme_counts"]["life_scenario_tasks"], 8)
        self.assertIn("mirix", memeye["upstream_method_configs"])
        self.assertIn("simplemem", memeye["upstream_method_configs"])
        raw = LOCK_PATH.read_text(encoding="utf-8")
        for banned in ("52.00", "62.00", "52.67", "%"):
            self.assertNotIn(banned, raw)

    def test_fault_fixture_covers_the_media_cases(self) -> None:
        script = ROOT / self.lock["fixture"]["builder"]
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cases"], self.lock["fixture"]["cases"])
        self.assertIn("ordered_text_image_text", payload["cases"])
        self.assertIn("jpeg_png_webp_mime", payload["cases"])
        self.assertIn("ten_mib_boundary", payload["cases"])
        self.assertIn("thirty_mib_boundary", payload["cases"])
        self.assertIn("invalid_base64", payload["cases"])
        self.assertIn("text_image_contradiction", payload["cases"])


if __name__ == "__main__":
    unittest.main()
