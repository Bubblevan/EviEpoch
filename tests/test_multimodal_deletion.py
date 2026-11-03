"""Deleting a source closes caption, OCR, observation, crop, and the original image."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.domain.content import data_uri_from_bytes, normalize_content
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline

_lifecycle_spec = importlib.util.spec_from_file_location(
    "test_lifecycle",
    Path(__file__).resolve().parent / "test_lifecycle.py",
)
fixtures = importlib.util.module_from_spec(_lifecycle_spec)
assert _lifecycle_spec and _lifecycle_spec.loader
_lifecycle_spec.loader.exec_module(fixtures)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
_CAPTION = "CAPTION9F3C the toolbox"
_OCR = "OCR9F3C"
_SCENE = "SCENE9F3C toolbox"
_CROP = "CROP9F3C"
_KEEP = "KEEP9F3C the toolbox"
_OTHER = "OTHER9F3C the toolbox"


class VisionProvider(fixtures.AnnotatedProviders):
    def __init__(self):
        super().__init__()
        self.visual_model_name = "test-vision"

    def visual_observation(self, data_uri, adjacent_text=""):
        if "CAPTION9F3C" not in adjacent_text:
            return {
                "scene": {"value": "kept scene", "origin": "VLM_observation"},
                "ocr": {"value": "", "origin": "OCR"},
            }
        return {
            "scene": {"value": _SCENE, "origin": "VLM_observation"},
            "ocr": {"value": _OCR, "origin": "OCR"},
        }


def _settings(folder: Path) -> Settings:
    return Settings(
        db_path=str(folder / "mm.sqlite3"),
        allow_sqlite=True,
        profile="full",
        task_adapter="multimodal",
        auth_scheme="none",
        media_store_path=str(folder / "media"),
        structured_ingest_enabled=False,
        rerank_enabled=False,
        window_radius=0,
        embed_dimensions=32,
        llm_api_key="test",
        embed_api_key="test",
        embed_base_url="https://example.test/v1",
    )


def _message(text: str, raw: bytes, when: int) -> Message:
    uri = data_uri_from_bytes(raw, "image/png")
    return Message(
        "user",
        text,
        when,
        envelope=normalize_content([
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": uri}},
        ]),
    )


def _files(folder: Path, user: str) -> list[Path]:
    root = folder / "media" / namespace(user)
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


class MultimodalDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.provider = VisionProvider()
        self.pipeline = MemoryPipeline(_settings(self.folder), providers=self.provider)
        self.pipeline.start()

    def tearDown(self) -> None:
        self.pipeline.close()
        self.temp.cleanup()

    def _add(self, request_id: str, user: str, text: str) -> None:
        added = self.pipeline.add(AddRequest(
            request_id,
            user,
            "sess",
            [_message(text, _TINY_PNG, 1714521600000)],
        ))
        self.assertTrue(added["success"])

    def _forget(self, user: str, token: str) -> None:
        instruction = f"Please forget {token}."

        def extraction(known):
            targets = [sid for sid, source in known.sources.items() if token in source.content]
            return {
                "nodes": [],
                "relations": [],
                "governance": [{
                    "message": 0,
                    "quote": instruction,
                    "action": "erase",
                    "target_ids": targets,
                }],
            }

        self.provider.documents[instruction] = extraction
        added = self.pipeline.add(AddRequest(
            f"forget-{user}",
            user,
            "sess",
            [Message("user", instruction, 1714521601000)],
        ))
        self.assertTrue(added["success"])

    def _search(self, user: str, query: str) -> str:
        result = self.pipeline.search(SearchRequest(query, user, 5))
        return json.dumps(result, ensure_ascii=False)

    def _active_text(self, user: str) -> str:
        ns = namespace(user)
        parts: list[str] = []
        with self.pipeline.store.transaction(ns) as c:
            for body, payload in c.execute(
                "SELECT body, payload FROM ee_objects WHERE ns=? AND active>0",
                (ns,),
            ).fetchall():
                parts.append(body or "")
                parts.append(payload or "")
            for (payload,) in c.execute("SELECT payload FROM ee_media_assets WHERE ns=?", (ns,)).fetchall():
                parts.append(payload or "")
        return "\n".join(parts)

    def _plant_crop(self, user: str, token: str) -> None:
        ns = namespace(user)
        crop = self.folder / "media" / ns / "crop" / f"{_CROP}.png"
        crop.parent.mkdir(parents=True, exist_ok=True)
        crop.write_bytes(_TINY_PNG)
        with self.pipeline.store.transaction(ns, True) as c:
            source_id = next(
                sid for sid, body in c.execute(
                    "SELECT id, body FROM ee_objects WHERE ns=? AND kind='source' AND active>0",
                    (ns,),
                ).fetchall()
                if token in (body or "")
            )
            asset_id = c.execute(
                "SELECT asset_id FROM ee_media_assets WHERE ns=?",
                (ns,),
            ).fetchone()[0]
            region_id = "crop-region-9f3c"
            payload = json.dumps({
                "asset_id": asset_id,
                "parent_asset_id": asset_id,
                "disclosable": True,
                "redaction": "complete",
                "transform": "crop",
                "crop_path": str(crop),
                "text": _CROP,
            })
            c.execute(
                "INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                "predicate_key,scope_key,length) VALUES(?,?,?,?,?,NULL,1,1,'','','',?)",
                (ns, region_id, "multimodal_region", payload, _CROP, len(_CROP)),
            )
            c.execute(
                "INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?)",
                (ns, region_id, source_id),
            )
            c.execute(
                "INSERT INTO ee_postings(ns,term,object_id,tf) VALUES(?,?,?,1)",
                (ns, _CROP.lower(), region_id),
            )

    def test_erase_hides_derivatives_and_keeps_another_namespace(self) -> None:
        self._add("mine", "user-forget", _CAPTION)
        self._add("other", "user-other", _OTHER)
        self._plant_crop("user-forget", _CAPTION)
        self.assertIn(_CAPTION, self._search("user-forget", _CAPTION))
        self.assertIn("image_url", self._search("user-forget", _OCR))
        self.assertTrue(self._search("user-forget", _CROP))
        self.assertIn(_CROP, self._active_text("user-forget"))
        self.assertTrue(_files(self.folder, "user-forget"))
        self.assertTrue(_files(self.folder, "user-other"))

        self._forget("user-forget", "CAPTION9F3C")

        forgotten = self._search("user-forget", _CAPTION)
        self.assertNotIn(_CAPTION, forgotten)
        self.assertNotIn("image_url", forgotten)
        self.assertNotIn(_OCR, self._search("user-forget", _OCR))
        self.assertNotIn(_SCENE, self._search("user-forget", _SCENE))
        self.assertNotIn(_CROP, self._search("user-forget", _CROP))
        readable = self._active_text("user-forget")
        for token in (_CAPTION, _OCR, _SCENE, _CROP):
            self.assertNotIn(token, readable)
        self.assertEqual(_files(self.folder, "user-forget"), [])
        kept = self._search("user-other", _OTHER)
        self.assertIn(_OTHER, kept)
        self.assertIn("image_url", kept)
        self.assertTrue(_files(self.folder, "user-other"))

    def test_same_namespace_copy_keeps_the_bytes(self) -> None:
        self._add("drop", "user-keep", _CAPTION)
        self._add("stay", "user-keep", _KEEP)
        self._forget("user-keep", "CAPTION9F3C")
        forgotten = self._search("user-keep", _CAPTION)
        self.assertNotIn(_CAPTION, forgotten)
        self.assertNotIn(_OCR, self._search("user-keep", _OCR))
        kept = self._search("user-keep", _KEEP)
        self.assertIn(_KEEP, kept)
        self.assertIn("image_url", kept)
        self.assertTrue(_files(self.folder, "user-keep"))

    def test_pending_redaction_hides_the_original_only(self) -> None:
        self._add("redact", "user-redact", _CAPTION)
        ns = namespace("user-redact")
        with self.pipeline.store.transaction(ns, True) as c:
            asset_id = c.execute("SELECT asset_id FROM ee_media_assets WHERE ns=?", (ns,)).fetchone()[0]
            source_id = c.execute(
                "SELECT id FROM ee_objects WHERE ns=? AND kind='source' AND active>0",
                (ns,),
            ).fetchone()[0]
            payload = json.dumps({
                "asset_id": asset_id,
                "disclosable": False,
                "redaction": "pending",
            })
            c.execute(
                "INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                "predicate_key,scope_key,length) VALUES(?,?,?,?,?,NULL,1,1,'','','',?)",
                (ns, "redact-region-9f3c", "multimodal_region", payload, "pending region", len("pending region")),
            )
            c.execute(
                "INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?)",
                (ns, "redact-region-9f3c", source_id),
            )
        found = json.loads(self._search("user-redact", _CAPTION))
        self.assertTrue(found["data"])
        content = found["data"][0]["content"]
        self.assertIn("text", {part.get("type") for part in content})
        self.assertNotIn("image_url", {part.get("type") for part in content})
        self.assertIn(_CAPTION, json.dumps(content))


if __name__ == "__main__":
    unittest.main()
