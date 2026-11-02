from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.multimodal.ingest import ingest_sources
from eviepoch.config import Settings
from eviepoch.domain import Source, stable_id, timestamp
from eviepoch.domain.content import data_uri_from_bytes, normalize_content
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
import importlib.util
from pathlib import Path as _Path

_spec = importlib.util.spec_from_file_location(
    "test_lifecycle",
    _Path(__file__).resolve().parent / "test_lifecycle.py",
)
fixtures = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(fixtures)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MultimodalMemoryTests(unittest.TestCase):
    def test_normalize_content_and_limits(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        env = normalize_content(
            [
                {"type": "text", "text": "蓝色工具箱在左侧。"},
                {"type": "image_url", "image_url": {"url": uri}},
            ]
        )
        self.assertTrue(env.has_media())
        self.assertIn("蓝色", env.text_for_extraction())

    def test_ingest_persists_asset_metadata(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        with tempfile.TemporaryDirectory() as tmp:
            config = Settings(
                db_path=":memory:",
                allow_sqlite=True,
                profile="hybrid",
                task_adapter="multimodal",
                media_store_path=str(Path(tmp) / "media"),
                llm_api_key="",
                embed_api_key="",
            )
            source = Source(
                stable_id("ns", "req", 0, "source-v1"),
                "ns",
                "req",
                "session",
                0,
                "user",
                "蓝色工具箱在左侧。",
                timestamp(1700000000000),
                "2024-01-01T00:00:00Z",
            )
            source.locator = {
                "content_envelope": normalize_content(
                    [
                        {"type": "text", "text": "蓝色工具箱在左侧。"},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]
                ).to_locator_dict(staging=True)
            }
            extension = ingest_sources([source], providers=None, config=config)
            self.assertTrue(any(a.kind == "multimodal_asset" for a in extension.artifacts))
            self.assertTrue(any(a.kind == "multimodal_observation" for a in extension.artifacts))

    def test_pipeline_round_trip_without_llm(self) -> None:
        uri = data_uri_from_bytes(_TINY_PNG, "image/png")
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "mm.sqlite3")
            config = Settings(
                db_path=db,
                allow_sqlite=True,
                profile="hybrid",
                task_adapter="multimodal",
                media_store_path=str(Path(tmp) / "media"),
                structured_ingest_enabled=False,
                rerank_enabled=False,
                embed_dimensions=32,
                llm_api_key="test",
                embed_api_key="test",
            )
            pipe = MemoryPipeline(config, providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                add = AddRequest(
                    "mm-req-1",
                    "user-mm",
                    "session-mm",
                    [
                        Message(
                            "user",
                            "请记住蓝色工具箱的位置。",
                            1700000000000,
                            envelope=normalize_content(
                                [
                                    {"type": "text", "text": "请记住蓝色工具箱的位置。"},
                                    {"type": "image_url", "image_url": {"url": uri}},
                                ]
                            ),
                        )
                    ],
                )
                self.assertTrue(pipe.add(add)["success"])
                response = pipe.search(SearchRequest("蓝色工具箱在哪里？", "user-mm", 3))
                self.assertTrue(response["data"])
                first = response["data"][0]
                self.assertIsInstance(first["content"], list)
                kinds = {part.get("type") for part in first["content"]}
                self.assertIn("text", kinds)
                self.assertIn("image_url", kinds)
            finally:
                pipe.close()


if __name__ == "__main__":
    unittest.main()
