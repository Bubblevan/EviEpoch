"""Change-question evidence sets and the search response media budget."""
from __future__ import annotations

import hashlib
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.multimodal.budget import MediaBudget, keep_whole_image
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
_PNG_B = _TINY_PNG + b"\x00"


def _wide_png(width: int, height: int) -> bytes:
    raw = bytearray(_TINY_PNG)
    struct.pack_into(">II", raw, 16, width, height)
    return bytes(raw)
_EARLIER = 1714521600000
_LATER = 1717200000000
_PHASES = [
    "search_index",
    "hydrate_original",
    "compare_visual_support",
    "find_counterevidence",
    "select",
]


def _settings(folder: Path, **overrides) -> Settings:
    values = dict(
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
        multimodal_arm="m1",
    )
    values.update(overrides)
    return Settings(**values)


def _add(pipe: MemoryPipeline, request_id: str, text: str, raw: bytes, when: int | None) -> None:
    uri = data_uri_from_bytes(raw, "image/png")
    added = pipe.add(AddRequest(
        request_id,
        "user-change",
        "session-change",
        [Message(
            "user",
            text,
            when,
            envelope=normalize_content([
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": uri}},
            ]),
        )],
    ))
    assert added["success"], added


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


def _source_ids(pipe: MemoryPipeline) -> set[str]:
    ns = namespace("user-change")
    with pipe.store.view(ns) as view:
        rows = view.c.execute(
            "SELECT id FROM ee_objects WHERE ns=? AND kind='source'",
            (ns,),
        ).fetchall()
    return {row[0] for row in rows}


class EvidenceSetBudgetTests(unittest.TestCase):
    def test_defaults_are_ten_and_thirty_mib(self) -> None:
        settings = Settings()
        self.assertEqual(settings.multimodal_max_image_bytes, 10 * 1024 * 1024)
        self.assertEqual(settings.multimodal_max_response_bytes, 30 * 1024 * 1024)

    def test_same_bytes_count_on_every_appearance(self) -> None:
        raw = _TINY_PNG
        digest = hashlib.sha256(raw).hexdigest()
        asset = {"content_hash": digest, "byte_length": len(raw), "mime": "image/png", "width": 1, "height": 1}
        settings = Settings(
            multimodal_max_response_bytes=len(raw) * 2,
            answer_input_tokens=117_760,
            answer_prompt_tokens=0,
            token_safety=1,
            embed_api_key="test",
            embed_base_url="https://example.test/v1",
        )
        budget = MediaBudget(settings, "toolbox", None)
        first = budget.allow_image(raw, "image/png", asset)
        second = budget.allow_image(raw, "image/png", asset)
        self.assertEqual(first, data_uri_from_bytes(raw, "image/png"))
        self.assertEqual(second, first)
        self.assertEqual(budget.decoded, len(raw) * 2)
        self.assertEqual(budget.appearances, 2)
        self.assertGreater(budget.wire_bytes, budget.decoded)

    def test_sliced_data_uri_is_dropped_whole(self) -> None:
        raw = _TINY_PNG
        url = data_uri_from_bytes(raw, "image/png")
        asset = {
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "byte_length": len(raw),
            "mime": "image/png",
            "width": 1,
            "height": 1,
        }
        self.assertEqual(keep_whole_image(url, asset), url)
        self.assertIsNone(keep_whole_image(url[:-4], asset))
        self.assertNotEqual(url[:-4], url)

    def test_complete_pair_is_one_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                _add(pipe, "change-earlier", "the same toolbox CHANGE9F3C was blue", _TINY_PNG, _EARLIER)
                _add(pipe, "change-later", "the same toolbox CHANGE9F3C is red", _PNG_B, _LATER)
                response, trace = pipe.search_with_trace(SearchRequest(
                    "did the same toolbox CHANGE9F3C change color",
                    "user-change",
                    1,
                ))
                visual = trace["multimodal"]
                report = visual["evidence_set"]
                self.assertEqual(visual["phases"], _PHASES)
                self.assertFalse(visual["caption_completes_visual"])
                self.assertTrue(report["admissible"])
                self.assertTrue(report["comparison_complete"])
                self.assertFalse(report["collapsed_identity"])
                self.assertFalse(report["S"]["attribute_comparison"]["supported"])
                self.assertTrue(report["checks"]["both_images_disclosable"])
                self.assertTrue(report["checks"]["each_image_has_its_own_time_support"])
                self.assertTrue(report["checks"]["entity_correspondence_is_supported"])
                self.assertTrue(report["checks"]["no_unresolved_identity_conflict_is_silently_collapsed"])
                self.assertEqual(report["H"]["unresolved_identity_conflicts"], [])
                self.assertEqual(report["gaps"], [])
                self.assertEqual(len(response["data"]), 1)
                item = response["data"][0]
                self.assertNotIn(item["id"], _source_ids(pipe))
                self.assertEqual(
                    [part["type"] for part in item["content"]],
                    ["text", "image_url", "text", "image_url"],
                )
                self.assertEqual(
                    _image_hashes(response["data"]),
                    [hashlib.sha256(_TINY_PNG).hexdigest(), hashlib.sha256(_PNG_B).hexdigest()],
                )
                self.assertIn("mm-visual-change", trace["covered_obligations"])
                self.assertGreater(visual["budget"]["wire_bytes"], visual["budget"]["decoded_image_bytes"])
            finally:
                pipe.close()

    def test_one_image_does_not_complete_the_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                _add(pipe, "change-later", "the same toolbox LATER9F3C is red", _PNG_B, _LATER)
                response, trace = pipe.search_with_trace(SearchRequest(
                    "did the same toolbox LATER9F3C change color",
                    "user-change",
                    3,
                ))
                report = trace["multimodal"]["evidence_set"]
                self.assertFalse(report["comparison_complete"])
                self.assertFalse(report["admissible"])
                self.assertTrue(report["single_image_is_not_a_comparison"])
                self.assertIn("image_at_t2", report["gaps"])
                self.assertFalse(trace["multimodal"]["visual_obligation_complete"])
                kinds = [item["kind"] for item in trace["multimodal"]["counterevidence"]]
                self.assertIn("later_image_is_not_a_completed_comparison", kinds)
                self.assertEqual(_image_hashes(response["data"]), [hashlib.sha256(_PNG_B).hexdigest()])
            finally:
                pipe.close()

    def test_missing_time_leaves_a_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                _add(pipe, "change-earlier", "the same toolbox TIME9F3C was blue", _TINY_PNG, _EARLIER)
                _add(pipe, "change-later", "the same toolbox TIME9F3C is red", _PNG_B, None)
                response, trace = pipe.search_with_trace(SearchRequest(
                    "did the same toolbox TIME9F3C change color",
                    "user-change",
                    1,
                ))
                report = trace["multimodal"]["evidence_set"]
                self.assertFalse(report["admissible"])
                self.assertFalse(report["comparison_complete"])
                self.assertIn("time_support_t2", report["gaps"])
                self.assertIsNone(report["S"]["time_support_t2"])
                self.assertEqual(len(response["data"]), 1)
                self.assertEqual(
                    _image_hashes(response["data"]),
                    [hashlib.sha256(_TINY_PNG).hexdigest(), hashlib.sha256(_PNG_B).hexdigest()],
                )
            finally:
                pipe.close()

    def test_unresolved_identity_is_not_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(_settings(folder), providers=fixtures.AnnotatedProviders())
            pipe.start()
            try:
                _add(pipe, "change-earlier", "a toolbox OTHER9F3C was blue", _TINY_PNG, _EARLIER)
                _add(pipe, "change-later", "a toolbox OTHER9F3C is red", _PNG_B, _LATER)
                _response, trace = pipe.search_with_trace(SearchRequest(
                    "did the toolbox OTHER9F3C change color",
                    "user-change",
                    1,
                ))
                report = trace["multimodal"]["evidence_set"]
                self.assertFalse(report["collapsed_identity"])
                self.assertFalse(report["admissible"])
                self.assertTrue(report["checks"]["no_unresolved_identity_conflict_is_silently_collapsed"])
                self.assertIn("unresolved_identity", report["gaps"])
                self.assertEqual(len(report["H"]["unresolved_identity_conflicts"]), 2)
            finally:
                pipe.close()

    def test_response_budget_omits_a_whole_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(
                _settings(folder, multimodal_max_response_bytes=len(_TINY_PNG)),
                providers=fixtures.AnnotatedProviders(),
            )
            pipe.start()
            try:
                _add(pipe, "change-earlier", "the same toolbox BUDGET9F3C was blue", _TINY_PNG, _EARLIER)
                _add(pipe, "change-later", "the same toolbox BUDGET9F3C is red", _PNG_B, _LATER)
                response, trace = pipe.search_with_trace(SearchRequest(
                    "did the same toolbox BUDGET9F3C change color",
                    "user-change",
                    1,
                ))
                visual = trace["multimodal"]
                report = visual["evidence_set"]
                kept = data_uri_from_bytes(_TINY_PNG, "image/png")
                dropped = data_uri_from_bytes(_PNG_B, "image/png")
                self.assertFalse(report["comparison_complete"])
                self.assertIn("image_at_t2", visual["gaps"])
                self.assertIn("response_media_budget", visual["gaps"])
                self.assertEqual(visual["budget"]["decoded_image_bytes"], len(_TINY_PNG))
                self.assertEqual(visual["budget"]["image_appearances"], 1)
                self.assertEqual(_image_hashes(response["data"]), [hashlib.sha256(_TINY_PNG).hexdigest()])
                urls = [
                    part["image_url"]["url"]
                    for part in response["data"][0]["content"]
                    if part.get("type") == "image_url"
                ]
                self.assertEqual(urls, [kept])
                self.assertNotIn(dropped, urls)
                self.assertNotIn(dropped[:-8], urls)
            finally:
                pipe.close()

    def test_answer_budget_does_not_slice_base64(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pipe = MemoryPipeline(
                _settings(
                    folder,
                    answer_input_tokens=1524,
                    answer_prompt_tokens=1024,
                    token_safety=1,
                ),
                providers=fixtures.AnnotatedProviders(),
            )
            pipe.start()
            try:
                _add(pipe, "color-1", "the toolbox ANSWER9F3C is red", _wide_png(512, 512), _EARLIER)
                response, trace = pipe.search_with_trace(SearchRequest(
                    "what color is the toolbox ANSWER9F3C",
                    "user-change",
                    3,
                ))
                visual = trace["multimodal"]
                self.assertEqual(visual["gaps"], ["original_image"])
                self.assertFalse(visual["visual_obligation_complete"])
                reasons = [item["reason"] for item in visual["budget"]["omitted"]]
                self.assertIn("answer_input_budget", reasons)
                self.assertEqual(_image_hashes(response["data"]), [])
                self.assertTrue(response["data"])
            finally:
                pipe.close()


if __name__ == "__main__":
    unittest.main()
