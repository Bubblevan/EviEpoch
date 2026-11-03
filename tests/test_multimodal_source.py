"""Ordered image-text becomes one source (Multimodal TRD step 2)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from eviepoch.api import create_app
from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.domain.content import (
    SourceEnvelope,
    data_uri_from_bytes,
    decode_data_uri,
    validate_media_limits,
)
from eviepoch.domain.requests import AddRequest, Message
from eviepoch.pipeline import MemoryPipeline

_spec = importlib.util.spec_from_file_location(
    "test_lifecycle",
    Path(__file__).resolve().parent / "test_lifecycle.py",
)
fixtures = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(fixtures)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _settings(folder: Path, adapter: str) -> Settings:
    return Settings(
        db_path=str(folder / "mm.sqlite3"),
        allow_sqlite=True,
        profile="hybrid",
        task_adapter=adapter,
        auth_scheme="none",
        media_store_path=str(folder / "media"),
        structured_ingest_enabled=False,
        rerank_enabled=False,
        embed_dimensions=32,
        llm_api_key="test",
        embed_api_key="test",
        embed_base_url="https://example.test/v1",
    )


def _rows(pipe: MemoryPipeline, user_id: str) -> dict:
    ns = namespace(user_id)
    with pipe.store.view(ns) as view:
        bodies = [row[0] for row in view.c.execute("SELECT body FROM ee_objects WHERE ns=?", (ns,)).fetchall()]
        terms = [row[0] for row in view.c.execute("SELECT term FROM ee_postings WHERE ns=?", (ns,)).fetchall()]
        sources = view.c.execute(
            "SELECT id, generation, payload FROM ee_objects WHERE ns=? AND kind='source'",
            (ns,),
        ).fetchall()
        occurrences = view.c.execute(
            "SELECT occurrence_id, source_id, part_ordinal, generation FROM ee_media_occurrences WHERE ns=?",
            (ns,),
        ).fetchall()
        assets = view.c.execute(
            "SELECT asset_id, content_hash, byte_length, generation FROM ee_media_assets WHERE ns=?",
            (ns,),
        ).fetchall()
    return {"bodies": bodies, "terms": terms, "sources": sources, "occurrences": occurrences, "assets": assets}


class OrderedSourceTests(unittest.TestCase):
    def test_length_precheck_rejects_before_decode(self) -> None:
        encoded = "A" * 8
        uri = f"data:image/png;base64,{encoded}"
        envelope = SourceEnvelope([{"type": "image_url", "image_url": {"url": uri}}])

        def fail_decode(*_args, **_kwargs):
            raise AssertionError("decoded the image before the length precheck")

        original = __import__("eviepoch.domain.content", fromlist=["base64"]).base64.b64decode
        content = __import__("eviepoch.domain.content", fromlist=["base64"])
        content.base64.b64decode = fail_decode
        try:
            with self.assertRaises(ValueError):
                validate_media_limits(envelope, max_single=4, max_total=4)
        finally:
            content.base64.b64decode = original

    def test_declared_mime_must_match_decoded_bytes(self) -> None:
        uri = data_uri_from_bytes(_JPEG, "image/jpeg").replace("image/jpeg", "image/png", 1)
        with self.assertRaisesRegex(ValueError, "MIME"):
            decode_data_uri(uri)

    def test_text_image_text_stays_one_ordered_source(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        marker = uri.split(",", 1)[1]
        with self._client("multimodal") as (client, pipe, folder):
            added = client.post("/add", json={
                "request_id": "order-1",
                "user_id": "user-order",
                "session_id": "session-order",
                "messages": [{
                    "role": "user",
                    "timestamp": 1700000000000,
                    "content": [
                        {"type": "text", "text": "BEFORE9F3C the toolbox"},
                        {"type": "image_url", "image_url": {"url": uri}},
                        {"type": "text", "text": "AFTER9F3C the shelf"},
                    ],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            found = client.post("/search", json={
                "query": "BEFORE9F3C",
                "user_id": "user-order",
                "top_k": 3,
            })
            self.assertEqual(found.status_code, 200, found.text)
            data = found.json()["data"]
            self.assertEqual(len(data), 1)
            content = data[0]["content"]
            self.assertEqual([part["type"] for part in content], ["text", "image_url", "text"])
            self.assertIn("BEFORE9F3C", content[0]["text"])
            self.assertIn("AFTER9F3C", content[2]["text"])
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            stored = _rows(pipe, "user-order")
            self.assertEqual(len(stored["sources"]), 1)
            payload = json.loads(stored["sources"][0][2])
            parts = payload["locator"]["content_envelope"]["parts"]
            self.assertEqual([part["type"] for part in parts], ["text", "image_url", "text"])
            self.assertEqual([part["part_ordinal"] for part in parts], [0, 1, 2])
            self.assertEqual(payload["ordinal"], 0)
            self.assertEqual(payload["role"], "user")
            self.assertEqual(payload["timestamp_value"], 1700000000000)
            self.assertEqual(len(stored["occurrences"]), 1)
            self.assertEqual(stored["occurrences"][0][2], 1)
            self.assertEqual(stored["sources"][0][1], stored["occurrences"][0][3])
            self.assertEqual(stored["assets"][0][1], hashlib.sha256(_TINY_PNG).hexdigest())
            self.assertEqual(stored["assets"][0][2], len(_TINY_PNG))
            self.assertEqual(stored["assets"][0][3], stored["sources"][0][1])
            indexed = "\n".join(stored["bodies"] + stored["terms"])
            self.assertNotIn(marker, indexed)
            self.assertNotIn(marker, stored["sources"][0][2])
            media = list(Path(folder, "media").rglob("*.png"))
            self.assertEqual(len(media), 1)
            self.assertEqual(media[0].read_bytes(), _TINY_PNG)

    def test_textual_adapter_rejects_the_array_and_does_not_store_it(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        with self._client("textual") as (client, pipe, _folder):
            rejected = client.post("/add", json={
                "request_id": "text-array",
                "user_id": "user-text",
                "session_id": "session-text",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ARRAYTEXT9F3C"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(rejected.status_code, 422)
            self.assertEqual(
                rejected.json()["detail"]["reason"],
                "ordered content arrays require EPOCH_TASK_ADAPTER=multimodal",
            )
            query = client.post("/search", json={
                "query": [
                    {"type": "image_url", "image_url": {"url": uri}},
                ],
                "user_id": "user-text",
                "top_k": 3,
            })
            self.assertEqual(query.status_code, 422)
            self.assertEqual(
                query.json()["detail"]["reason"],
                "ordered query arrays require EPOCH_TASK_ADAPTER=multimodal",
            )
            kept = client.post("/add", json={
                "request_id": "text-plain",
                "user_id": "user-text",
                "session_id": "session-text",
                "messages": [{"role": "user", "content": "plain textual memory 9f3c"}],
            })
            self.assertEqual(kept.status_code, 200, kept.text)
            found = client.post("/search", json={
                "query": "plain textual memory",
                "user_id": "user-text",
                "top_k": 3,
            })
            self.assertEqual(found.status_code, 200, found.text)
            self.assertEqual(len(found.json()["data"]), 1)
            stored = _rows(pipe, "user-text")
            blob = "\n".join(stored["bodies"] + [row[2] for row in stored["sources"]])
            self.assertNotIn("ARRAYTEXT9F3C", blob)
            self.assertNotIn(uri, blob)
            self.assertEqual(stored["occurrences"], [])
            self.assertEqual(stored["assets"], [])
            with self.assertRaisesRegex(ValueError, "EPOCH_TASK_ADAPTER=multimodal"):
                pipe.add(AddRequest(
                    "text-envelope",
                    "user-text",
                    "session-text",
                    [Message("user", "ARRAYTEXT9F3C", 1700000000000, envelope=SourceEnvelope([
                        {"type": "text", "text": "ARRAYTEXT9F3C"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]))],
                ))

    def test_query_image_is_not_stored(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        query_uri = data_uri_from_bytes(_JPEG, "image/jpeg")
        query_hash = hashlib.sha256(_JPEG).hexdigest()
        with self._client("multimodal") as (client, pipe, folder):
            added = client.post("/add", json={
                "request_id": "kept-1",
                "user_id": "user-query",
                "session_id": "session-query",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "remember the blue toolbox"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            before = _rows(pipe, "user-query")
            files_before = sorted(path.name for path in Path(folder, "media").rglob("*") if path.is_file())
            found = client.post("/search", json={
                "query": [
                    {"type": "text", "text": "blue toolbox"},
                    {"type": "image_url", "image_url": {"url": query_uri}},
                ],
                "user_id": "user-query",
                "top_k": 3,
            })
            self.assertEqual(found.status_code, 200, found.text)
            after = _rows(pipe, "user-query")
            files_after = sorted(path.name for path in Path(folder, "media").rglob("*") if path.is_file())
            self.assertEqual(before["occurrences"], after["occurrences"])
            self.assertEqual(before["assets"], after["assets"])
            self.assertEqual(before["sources"], after["sources"])
            self.assertEqual(before["bodies"], after["bodies"])
            self.assertEqual(files_before, files_after)
            self.assertNotIn(query_hash, {row[1] for row in after["assets"]})
            self.assertTrue(found.json()["data"])

    def test_image_only_message_becomes_a_source(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        with self._client("multimodal") as (client, pipe, _folder):
            added = client.post("/add", json={
                "request_id": "image-only-1",
                "user_id": "user-image",
                "session_id": "session-image",
                "messages": [{
                    "role": "user",
                    "timestamp": 1700000001000,
                    "content": [{"type": "image_url", "image_url": {"url": uri}}],
                }],
            })
            self.assertEqual(added.status_code, 200, added.text)
            self.assertNotIn("url", added.json())
            found = client.post("/search", json={
                "query": "image-only",
                "user_id": "user-image",
                "top_k": 3,
            })
            self.assertEqual(found.status_code, 200, found.text)
            content = found.json()["data"][0]["content"]
            self.assertEqual([part["type"] for part in content], ["image_url"])
            stored = _rows(pipe, "user-image")
            self.assertEqual(len(stored["sources"]), 1)
            payload = json.loads(stored["sources"][0][2])
            parts = payload["locator"]["content_envelope"]["parts"]
            self.assertEqual([part["type"] for part in parts], ["image_url"])
            self.assertEqual(parts[0]["part_ordinal"], 0)
            self.assertEqual(len(stored["occurrences"]), 1)
            self.assertEqual(stored["occurrences"][0][1], stored["sources"][0][0])
            self.assertEqual(stored["sources"][0][1], stored["occurrences"][0][3])

    def _client(self, adapter: str):
        return _Client(adapter)


class _Client:
    def __init__(self, adapter: str):
        self.adapter = adapter

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        config = _settings(folder, self.adapter)
        self.pipe = MemoryPipeline(config, providers=fixtures.AnnotatedProviders())
        self.client = TestClient(create_app(config, self.pipe))
        self.client.__enter__()
        return self.client, self.pipe, folder

    def __exit__(self, exc_type, exc, tb):
        self.client.__exit__(exc_type, exc, tb)
        self._tmp.cleanup()
        return False


if __name__ == "__main__":
    unittest.main()
