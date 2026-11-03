"""Observation fields keep their origins and are not folded into one caption."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from eviepoch.api import create_app
from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.domain.content import data_uri_from_bytes
from eviepoch.pipeline import MemoryPipeline

_lifecycle_spec = importlib.util.spec_from_file_location(
    "test_lifecycle",
    Path(__file__).resolve().parent / "test_lifecycle.py",
)
fixtures = importlib.util.module_from_spec(_lifecycle_spec)
assert _lifecycle_spec and _lifecycle_spec.loader
_lifecycle_spec.loader.exec_module(fixtures)

_identity_spec = importlib.util.spec_from_file_location(
    "test_multimodal_identity",
    Path(__file__).resolve().parent / "test_multimodal_identity.py",
)
identity = importlib.util.module_from_spec(_identity_spec)
assert _identity_spec and _identity_spec.loader
_identity_spec.loader.exec_module(identity)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


class VisionProvider(fixtures.AnnotatedProviders):
    def __init__(self, result=None, fail=False):
        super().__init__()
        self.result = result
        self.fail = fail
        self.calls = 0
        self.visual_model_name = "test-vision"

    def visual_observation(self, data_uri, adjacent_text=""):
        self.calls += 1
        if self.fail:
            raise RuntimeError("vision unavailable")
        return self.result


def _settings(folder: Path, *, caption_only: bool = False) -> Settings:
    return Settings(
        db_path=str(folder / "mm.sqlite3"),
        allow_sqlite=True,
        profile="hybrid",
        task_adapter="multimodal",
        auth_scheme="none",
        media_store_path=str(folder / "media"),
        structured_ingest_enabled=False,
        rerank_enabled=False,
        embed_dimensions=32,
        llm_api_key="test",
        embed_api_key="test",
        embed_base_url="https://example.test/v1",
        multimodal_caption_only=caption_only,
    )


def _observation(pipe: MemoryPipeline, user_id: str) -> dict | None:
    ns = namespace(user_id)
    with pipe.store.view(ns) as view:
        row = view.c.execute(
            "SELECT payload FROM ee_objects WHERE ns=? AND kind='multimodal_observation'",
            (ns,),
        ).fetchone()
    return json.loads(row[0]) if row else None


class ObservationFieldTests(unittest.TestCase):
    def test_missing_model_leaves_visual_fields_empty(self) -> None:
        raw = identity._jpeg("2018:06:01 10:00:00", "2019-04-02")
        uri = data_uri_from_bytes(raw, "image/jpeg")
        with _Client(fixtures.AnnotatedProviders()) as (client, pipe, _folder):
            added = client.post("/add", json={
                "request_id": "obs-empty",
                "user_id": "user-obs",
                "session_id": "session-obs",
                "messages": [{
                    "role": "user",
                    "timestamp": 1714521600000,
                    "content": [
                        {"type": "text", "text": "the toolbox is red"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            payload = _observation(pipe, "user-obs")
            fields = payload["fields"]
            self.assertEqual(fields["supplied_text"]["parts"], ["the toolbox is red"])
            self.assertEqual(fields["supplied_text"]["origin"], "supplied_text")
            self.assertEqual(fields["scene"]["value"], "")
            self.assertIsNone(fields["scene"]["origin"])
            self.assertFalse(fields["scene"]["extracted"])
            self.assertEqual(fields["ocr"]["value"], "")
            self.assertIsNone(fields["ocr"]["origin"])
            self.assertFalse(fields["ocr"]["extracted"])
            self.assertEqual(fields["exif"]["value"], "2018-06-01T10:00:00")
            self.assertEqual(fields["exif"]["origin"], "exif")
            self.assertNotIn("2018", fields["supplied_text"]["value"])
            self.assertNotIn("red", fields["exif"]["value"])
            self.assertNotIn("caption", fields)
            self.assertIsNone(payload["ocr_span"])
            self.assertFalse(payload["compared_to_original"])
            self.assertIsNone(payload["model"])
            self.assertIsNone(payload["prompt_id"])
            self.assertEqual(payload["experiment_arm"], "multimodal")
            self.assertEqual(payload["fields"]["status"]["vision"], "unavailable")
            self.assertEqual(payload["representation"]["width"], 2)
            self.assertEqual(payload["representation"]["height"], 1)
            self.assertIsNone(payload["representation"]["rotation"])
            self.assertFalse(payload["representation"]["extracted"])

    def test_user_text_ocr_and_model_observation_stay_separate(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        provider = VisionProvider({
            "caption": "the toolbox is red and also blue BOX-17",
            "scene": {"value": "blue toolbox", "origin": "VLM_observation"},
            "ocr": {"value": "BOX-17", "origin": "OCR", "confidence": 0.4},
            "colors": [{"name": "blue", "region": "left", "origin": "VLM_observation"}],
            "scene_from_user": {"value": "the toolbox is red", "origin": "supplied_text"},
        })
        with _Client(provider) as (client, pipe, _folder):
            added = client.post("/add", json={
                "request_id": "obs-split",
                "user_id": "user-split",
                "session_id": "session-split",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "the toolbox is red"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            self.assertEqual(provider.calls, 1)
            payload = _observation(pipe, "user-split")
            fields = payload["fields"]
            self.assertEqual(fields["supplied_text"]["value"], "the toolbox is red")
            self.assertEqual(fields["supplied_text"]["origin"], "supplied_text")
            self.assertEqual(fields["scene"]["value"], "blue toolbox")
            self.assertEqual(fields["scene"]["origin"], "VLM_observation")
            self.assertEqual(fields["ocr"]["value"], "BOX-17")
            self.assertEqual(fields["ocr"]["origin"], "OCR")
            self.assertEqual(fields["colors"][0]["name"], "blue")
            self.assertEqual(fields["colors"][0]["region"], "left")
            self.assertNotIn("caption", fields)
            self.assertNotIn("red", fields["scene"]["value"])
            self.assertNotIn("blue", fields["supplied_text"]["value"])
            self.assertNotIn("BOX-17", fields["supplied_text"]["value"])
            self.assertNotIn("red", fields["ocr"]["value"])
            self.assertEqual(payload["ocr_span"]["text"], "BOX-17")
            self.assertEqual(payload["ocr_span"]["origin"], "OCR")
            self.assertIsNone(payload["ocr_span"]["bbox"])
            self.assertTrue(payload["compared_to_original"])
            self.assertEqual(payload["model"], "test-vision")
            self.assertEqual(payload["prompt_id"], "sgm-v1")
            self.assertEqual(payload["representation"]["model"], "test-vision")
            self.assertEqual(payload["experiment_arm"], "multimodal")
            self.assertNotEqual(fields["supplied_caption"]["value"], fields["scene"]["value"])

    def test_vision_failure_is_not_a_caption_only_success(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        provider = VisionProvider(fail=True)
        with _Client(provider) as (client, pipe, _folder):
            failed = client.post("/add", json={
                "request_id": "obs-fail",
                "user_id": "user-fail",
                "session_id": "session-fail",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "the toolbox is red"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(failed.status_code, 422)
            self.assertIn("caption-only", failed.json()["detail"]["reason"])
            self.assertIsNone(_observation(pipe, "user-fail"))

    def test_caption_only_arm_is_explicit_and_does_not_call_the_model(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        provider = VisionProvider({"scene": {"value": "invented blue", "origin": "VLM_observation"}})
        with _Client(provider, caption_only=True) as (client, pipe, _folder):
            added = client.post("/add", json={
                "request_id": "obs-caption",
                "user_id": "user-caption",
                "session_id": "session-caption",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "the toolbox is red"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            self.assertEqual(provider.calls, 0)
            payload = _observation(pipe, "user-caption")
            self.assertEqual(payload["experiment_arm"], "caption_only")
            self.assertEqual(payload["fields"]["status"]["vision"], "not_called")
            self.assertEqual(payload["fields"]["supplied_text"]["value"], "the toolbox is red")
            self.assertEqual(payload["fields"]["scene"]["value"], "")
            self.assertFalse(payload["fields"]["scene"]["extracted"])
            self.assertFalse(payload["compared_to_original"])
            self.assertIsNone(payload["model"])


class _Client:
    def __init__(self, providers, *, caption_only: bool = False):
        self.providers = providers
        self.caption_only = caption_only

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        config = _settings(folder, caption_only=self.caption_only)
        self.pipe = MemoryPipeline(config, providers=self.providers)
        self.client = TestClient(create_app(config, self.pipe))
        self.client.__enter__()
        return self.client, self.pipe, folder

    def __exit__(self, exc_type, exc, tb):
        self.client.__exit__(exc_type, exc, tb)
        self._tmp.cleanup()
        return False


if __name__ == "__main__":
    unittest.main()
