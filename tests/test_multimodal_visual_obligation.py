"""Color, position, and in-image text stay open until the original image is returned."""
from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.domain.content import data_uri_from_bytes, decode_data_uri, normalize_content
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
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
_CAPTION = "the toolbox is red"
_PHASES = [
    "search_index",
    "hydrate_original",
    "compare_visual_support",
    "find_counterevidence",
    "select",
]


def _settings(folder: Path, arm: str) -> Settings:
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
        multimodal_arm=arm,
    )


def _counts(pipe: MemoryPipeline, user_id: str) -> tuple[list, list]:
    ns = namespace(user_id)
    with pipe.store.view(ns) as view:
        occurrences = view.c.execute(
            "SELECT occurrence_id FROM ee_media_occurrences WHERE ns=?",
            (ns,),
        ).fetchall()
        assets = view.c.execute(
            "SELECT content_hash FROM ee_media_assets WHERE ns=?",
            (ns,),
        ).fetchall()
    return occurrences, assets


def _image_hashes(data: list[dict]) -> list[str]:
    found = []
    for item in data:
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") != "image_url":
                continue
            _mime, raw = decode_data_uri(part["image_url"]["url"])
            found.append(hashlib.sha256(raw).hexdigest())
    return found


class VisualObligationTests(unittest.TestCase):
    def _add(self, pipe: MemoryPipeline) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        added = pipe.add(AddRequest(
            "visual-req",
            "user-visual",
            "session-visual",
            [Message(
                "user",
                _CAPTION,
                1714521600000,
                envelope=normalize_content([
                    {"type": "text", "text": _CAPTION},
                    {"type": "image_url", "image_url": {"url": uri}},
                ]),
            )],
        ))
        self.assertTrue(added["success"])

    def test_caption_does_not_complete_color_position_or_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder, "m1"), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                self._add(pipe)
                asset_hash = hashlib.sha256(_TINY_PNG).hexdigest()
                _occurrences, assets = _counts(pipe, "user-visual")
                self.assertEqual([row[0] for row in assets], [asset_hash])
                questions = {
                    "color": "what color is the toolbox",
                    "position": "where is the toolbox",
                    "ocr": "what text is in the image toolbox",
                }
                for kind, query in questions.items():
                    response, trace = pipe.search_with_trace(SearchRequest(query, "user-visual", 3))
                    visual = trace["multimodal"]
                    self.assertEqual(visual["phases"], _PHASES)
                    self.assertFalse(visual["visual_embedding"])
                    self.assertTrue(visual["caption_hit"])
                    self.assertFalse(visual["caption_completes_visual"])
                    self.assertEqual(visual["counterevidence"], [{"kind": "caption_not_visual_proof"}])
                    self.assertTrue(visual["visual_obligation_complete"])
                    self.assertEqual(visual["gaps"], [])
                    self.assertIn(f"mm-visual-{kind}", trace["covered_obligations"])
                    self.assertNotIn(f"mm-visual-{kind}", trace["missing_obligations"])
                    returned = _image_hashes(response["data"])
                    self.assertEqual(returned, [asset_hash])
                    self.assertEqual(visual["returned_image_hashes"], [asset_hash])
            finally:
                pipe.close()

            withheld = MemoryPipeline(_settings(folder, "m0"), providers=fixtures.AnnotatedProviders())
            withheld.start()
            try:
                response, trace = withheld.search_with_trace(
                    SearchRequest("what color is the toolbox", "user-visual", 3)
                )
                visual = trace["multimodal"]
                self.assertEqual(visual["phases"], _PHASES)
                self.assertFalse(visual["visual_embedding"])
                self.assertTrue(visual["caption_hit"])
                self.assertFalse(visual["caption_completes_visual"])
                self.assertFalse(visual["original_returned"])
                self.assertFalse(visual["visual_obligation_complete"])
                self.assertEqual(visual["gaps"], ["original_image"])
                self.assertIn("mm-visual-color", trace["missing_obligations"])
                self.assertNotIn("mm-visual-color", trace["covered_obligations"])
                self.assertEqual(_image_hashes(response["data"]), [])
                for item in response["data"]:
                    content = item["content"]
                    if isinstance(content, list):
                        self.assertNotIn("image_url", {part.get("type") for part in content})
            finally:
                withheld.close()

    def test_query_image_hash_uses_bytes_and_is_not_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder, "m1"), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                self._add(pipe)
                before_occurrences, before_assets = _counts(pipe, "user-visual")
                files_before = sorted(path.name for path in (folder / "media").rglob("*") if path.is_file())
                query_uri = data_uri_from_bytes(_JPEG, "image/jpeg")
                caption = "what color is the toolbox"
                response, trace = pipe.search_with_trace(SearchRequest(
                    [
                        {"type": "text", "text": caption},
                        {"type": "image_url", "image_url": {"url": query_uri}},
                    ],
                    "user-visual",
                    3,
                ))
                bundle = trace["multimodal"]["query_bundle"]
                query_hash = hashlib.sha256(_JPEG).hexdigest()
                self.assertEqual(bundle["parts"], ["text", "image_url"])
                self.assertEqual(bundle["image_hashes"], [query_hash])
                self.assertNotIn("", bundle["image_hashes"])
                self.assertNotIn(hashlib.sha256(b"").hexdigest(), bundle["image_hashes"])
                self.assertNotIn(hashlib.sha256(caption.encode()).hexdigest(), bundle["image_hashes"])
                self.assertFalse(trace["multimodal"]["visual_embedding"])
                self.assertTrue(response["data"])
                after_occurrences, after_assets = _counts(pipe, "user-visual")
                files_after = sorted(path.name for path in (folder / "media").rglob("*") if path.is_file())
                self.assertEqual(before_occurrences, after_occurrences)
                self.assertEqual(before_assets, after_assets)
                self.assertEqual(files_before, files_after)
                self.assertNotIn(query_hash, {row[0] for row in after_assets})
            finally:
                pipe.close()

    def test_hash_mismatch_does_not_complete_the_obligation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder, "m1"), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                self._add(pipe)
                stored = [path for path in (folder / "media").rglob("*") if path.is_file()]
                self.assertEqual(len(stored), 1)
                stored[0].write_bytes(stored[0].read_bytes() + b"\x00")
                response, trace = pipe.search_with_trace(
                    SearchRequest("what color is the toolbox", "user-visual", 3)
                )
                visual = trace["multimodal"]
                self.assertFalse(visual["caption_completes_visual"])
                self.assertFalse(visual["visual_obligation_complete"])
                self.assertFalse(visual["original_returned"])
                self.assertEqual(visual["gaps"], ["original_image"])
                self.assertIn("mm-visual-color", trace["missing_obligations"])
                self.assertEqual(_image_hashes(response["data"]), [])
            finally:
                pipe.close()


if __name__ == "__main__":
    unittest.main()
