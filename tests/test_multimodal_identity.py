"""Asset identity, separate occurrences, and separated image times."""
from __future__ import annotations

import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from eviepoch.api import create_app
from eviepoch.config import Settings
from eviepoch.domain import namespace, timestamp
from eviepoch.domain.content import data_uri_from_bytes
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
_FIRST = 1714521600000
_SECOND = 1717200000000


def _jpeg(exif_clock: str, depicted: str) -> bytes:
    clock = exif_clock.encode("ascii") + b"\x00"
    sub_off = 8 + 2 + 12 + 4
    ascii_off = sub_off + 2 + 12 + 4
    tiff = b"II" + struct.pack("<HI", 42, 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, sub_off) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(clock), ascii_off) + struct.pack("<I", 0)
    tiff += clock
    exif = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(exif)) + exif
    comment = depicted.encode("ascii")
    com = b"\xff\xfe" + struct.pack(">H", 2 + len(comment)) + comment
    frame = struct.pack(">BHHB", 8, 1, 2, 1) + bytes([1, 0x11, 0])
    sof = b"\xff\xc0" + struct.pack(">H", 2 + len(frame)) + frame
    return b"\xff\xd8" + app1 + com + sof + b"\xff\xd9"


def _settings(folder: Path) -> Settings:
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
    )


def _media(pipe: MemoryPipeline, user_id: str) -> dict:
    ns = namespace(user_id)
    with pipe.store.view(ns) as view:
        assets = [
            {"asset_id": row[0], "content_hash": row[1], "path": row[2], "payload": json.loads(row[3])}
            for row in view.c.execute(
                "SELECT asset_id, content_hash, storage_path, payload FROM ee_media_assets WHERE ns=?",
                (ns,),
            ).fetchall()
        ]
        occurrences = [
            json.loads(row[0])
            for row in view.c.execute(
                "SELECT payload FROM ee_media_occurrences WHERE ns=? ORDER BY part_ordinal, source_id",
                (ns,),
            ).fetchall()
        ]
    return {"ns": ns, "assets": assets, "occurrences": occurrences}


class MediaIdentityTests(unittest.TestCase):
    def test_same_bytes_share_one_asset_and_another_namespace_does_not(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        with _Client() as (client, pipe, folder):
            added = client.post("/add", json={
                "request_id": "same-bytes",
                "user_id": "user-same",
                "session_id": "session-same",
                "messages": [
                    {"role": "user", "timestamp": _FIRST, "content": [
                        {"type": "text", "text": "first appearance ALPHA9F3C"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]},
                    {"role": "user", "timestamp": _SECOND, "content": [
                        {"type": "text", "text": "second appearance BETA9F3C"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]},
                ],
            })
            self.assertEqual(added.status_code, 200, added.text)
            same = _media(pipe, "user-same")
            self.assertEqual(len(same["assets"]), 1)
            self.assertEqual(len(same["occurrences"]), 2)
            asset = same["assets"][0]["payload"]
            self.assertEqual(asset["persistence"], "published")
            self.assertEqual(asset["namespace"], same["ns"])
            self.assertEqual(asset["width"], 1)
            self.assertEqual(asset["height"], 1)
            self.assertEqual(asset["decoded_bytes"], len(_TINY_PNG))
            self.assertEqual(asset["real_mime"], "image/png")
            self.assertEqual(sorted(asset["source_ids"]), sorted(item["source_id"] for item in same["occurrences"]))
            self.assertEqual({item["asset_id"] for item in same["occurrences"]}, {same["assets"][0]["asset_id"]})
            times = [item["message_time"] for item in same["occurrences"]]
            self.assertEqual(sorted(times), sorted([timestamp(_FIRST), timestamp(_SECOND)]))
            for item in same["occurrences"]:
                self.assertIsNone(item["depicted_date"])
                self.assertIsNone(item["exif_time"])
                self.assertIsNone(item["captured_at"])
                self.assertIsNone(item["claimed_event_time"])
                self.assertNotEqual(item["stored_at"], item["message_time"])
                self.assertNotEqual(item["message_time"], item["captured_at"])
            self.assertEqual(len(list(Path(folder, "media").rglob("*.png"))), 1)

            other = client.post("/add", json={
                "request_id": "other-ns",
                "user_id": "user-other",
                "session_id": "session-other",
                "messages": [{
                    "role": "user",
                    "timestamp": _FIRST,
                    "content": [
                        {"type": "text", "text": "ONLYOTHER9F3C"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(other.status_code, 200, other.text)
            owned = _media(pipe, "user-same")
            foreign = _media(pipe, "user-other")
            self.assertNotEqual(owned["ns"], foreign["ns"])
            self.assertNotEqual(owned["assets"][0]["asset_id"], foreign["assets"][0]["asset_id"])
            self.assertNotEqual(owned["assets"][0]["path"], foreign["assets"][0]["path"])
            self.assertEqual(owned["assets"][0]["content_hash"], foreign["assets"][0]["content_hash"])
            self.assertTrue(Path(owned["assets"][0]["path"]).is_file())
            self.assertTrue(Path(foreign["assets"][0]["path"]).is_file())
            hidden = client.post("/search", json={"query": "ONLYOTHER9F3C", "user_id": "user-same", "top_k": 3})
            self.assertEqual(hidden.status_code, 200, hidden.text)
            self.assertEqual(hidden.json()["data"], [])
            visible = client.post("/search", json={"query": "ONLYOTHER9F3C", "user_id": "user-other", "top_k": 3})
            self.assertEqual(visible.status_code, 200, visible.text)
            self.assertTrue(visible.json()["data"])

    def test_depicted_date_exif_message_time_and_stored_at_stay_apart(self) -> None:
        raw = _jpeg("2018:06:01 10:00:00", "2019-04-02")
        uri = data_uri_from_bytes(raw, "image/jpeg")
        with _Client() as (client, pipe, _folder):
            first = client.post("/add", json={
                "request_id": "time-1",
                "user_id": "user-time",
                "session_id": "session-time",
                "messages": [{
                    "role": "user",
                    "timestamp": _FIRST,
                    "content": [
                        {"type": "text", "text": "the screenshot shows an older chat"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(first.status_code, 200, first.text)
            second = client.post("/add", json={
                "request_id": "time-2",
                "user_id": "user-time",
                "session_id": "session-time",
                "messages": [{
                    "role": "user",
                    "timestamp": _SECOND,
                    "content": [
                        {"type": "text", "text": "the same picture was sent again"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ],
                }],
            })
            self.assertEqual(second.status_code, 200, second.text)
            media = _media(pipe, "user-time")
            self.assertEqual(len(media["assets"]), 1)
            self.assertEqual(len(media["occurrences"]), 2)
            asset = media["assets"][0]["payload"]
            self.assertEqual(asset["depicted_date"], "2019-04-02")
            self.assertEqual(asset["exif_time"], "2018-06-01T10:00:00")
            self.assertEqual(asset["captured_at"], "2018-06-01T10:00:00")
            self.assertEqual(asset["width"], 2)
            self.assertEqual(asset["height"], 1)
            self.assertNotIn("message_time", asset)
            self.assertNotIn("stored_at", asset)
            by_message = {item["message_time"]: item for item in media["occurrences"]}
            self.assertEqual(set(by_message), {timestamp(_FIRST), timestamp(_SECOND)})
            for item in media["occurrences"]:
                self.assertEqual(item["depicted_date"], "2019-04-02")
                self.assertEqual(item["exif_time"], "2018-06-01T10:00:00")
                self.assertEqual(item["captured_at"], "2018-06-01T10:00:00")
                self.assertIsNone(item["claimed_event_time"])
                self.assertNotEqual(item["depicted_date"], item["stored_at"])
                self.assertNotEqual(item["exif_time"], item["message_time"])
                self.assertNotEqual(item["message_time"], item["stored_at"])
                self.assertNotEqual(item["depicted_date"], item["exif_time"][:10])
            self.assertEqual(len(list(Path(media["assets"][0]["path"]).parent.glob("*.jpg"))), 1)


class _Client:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        config = _settings(folder)
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
