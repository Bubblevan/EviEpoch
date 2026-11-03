"""Local image fault specimens for Multimodal TRD §22.4.

The command `python experiments/multimodal/fixtures/fault_media.py` checks
every specimen against the content contract and exits 0. It does not download
MemEye, ATM-Bench, or Mem-Gallery data.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eviepoch.domain import stable_id
from eviepoch.domain.content import (
    data_uri_from_bytes,
    decode_data_uri,
    normalize_content,
    query_text,
    validate_media_limits,
)

SINGLE = 10 * 1024 * 1024
TOTAL = 30 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "


def _uri(raw: bytes, mime: str) -> str:
    return data_uri_from_bytes(raw, mime)


def _padded(prefix: bytes, size: int) -> bytes:
    if size < len(prefix):
        raise RuntimeError("padding shorter than magic")
    return prefix + b"\x00" * (size - len(prefix))


def _must_raise(label: str, fn) -> None:
    try:
        fn()
    except ValueError:
        return
    raise RuntimeError(f"{label} was accepted")


def verify() -> list[str]:
    cases: list[str] = []

    ordered = normalize_content(
        [
            {"type": "text", "text": "before the picture"},
            {"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}},
            {"type": "text", "text": "after the picture"},
        ]
    )
    if [part["type"] for part in ordered.parts] != ["text", "image_url", "text"]:
        raise RuntimeError("text-image-text order was not preserved")
    if ordered.parts[0]["text"] != "before the picture" or ordered.parts[2]["text"] != "after the picture":
        raise RuntimeError("text parts were rewritten")
    cases.append("ordered_text_image_text")

    many = normalize_content(
        [
            {"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}},
            {"type": "image_url", "image_url": {"url": _uri(JPEG, "image/jpeg")}},
        ]
    )
    if sum(part["type"] == "image_url" for part in many.parts) != 2:
        raise RuntimeError("multiple images collapsed")
    cases.append("multiple_images")

    image_query = normalize_content(
        [{"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}}]
    )
    if image_query.text_for_extraction().strip():
        raise RuntimeError("image-only query invented text")
    if query_text([{"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}}]) != "[image query]":
        raise RuntimeError("image-only query was not marked as an image query")
    cases.append("image_only_query")

    no_caption = {
        "parts": [{"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}}],
        "caption": None,
    }
    if no_caption["caption"] is not None or any(part.get("type") == "text" for part in no_caption["parts"]):
        raise RuntimeError("caption was invented for an image-only source")
    cases.append("image_without_caption")

    magic = {"image/png": PNG[:8], "image/jpeg": JPEG[:3], "image/webp": b"RIFF"}
    for mime, raw in (("image/png", PNG), ("image/jpeg", JPEG), ("image/webp", WEBP)):
        declared, decoded = decode_data_uri(_uri(raw, mime))
        if declared != mime or not decoded.startswith(magic[mime]):
            raise RuntimeError(f"{mime} magic does not match the declared type")
    cases.append("jpeg_png_webp_mime")

    limit = {"max_single": SINGLE, "max_total": TOTAL}
    validate_media_limits(
        normalize_content([{"type": "image_url", "image_url": {"url": _uri(_padded(PNG, SINGLE), "image/png")}}]),
        **limit,
    )
    _must_raise(
        "single image past 10 MiB",
        lambda: validate_media_limits(
            normalize_content(
                [{"type": "image_url", "image_url": {"url": _uri(_padded(PNG, SINGLE + 1), "image/png")}}]
            ),
            **limit,
        ),
    )
    cases.append("ten_mib_boundary")

    three = [
        {"type": "image_url", "image_url": {"url": _uri(_padded(PNG, SINGLE), "image/png")}}
        for _ in range(3)
    ]
    validate_media_limits(normalize_content(three), **limit)
    _must_raise(
        "add total past 30 MiB",
        lambda: validate_media_limits(
            normalize_content(
                three
                + [{"type": "image_url", "image_url": {"url": _uri(PNG, "image/png")}}]
            ),
            **limit,
        ),
    )
    cases.append("thirty_mib_boundary")

    _must_raise(
        "invalid base64",
        lambda: decode_data_uri("data:image/png;base64,****"),
    )
    cases.append("invalid_base64")
    _must_raise(
        "remote url",
        lambda: decode_data_uri("https://example.test/photo.png"),
    )
    cases.append("remote_url_rejected")

    contradiction = {
        "supplied_text": {"value": "the toolbox is red", "origin": "supplied_text"},
        "visual": {"value": "blue toolbox", "origin": "VLM_observation"},
    }
    if contradiction["supplied_text"]["value"] == contradiction["visual"]["value"]:
        raise RuntimeError("contradictory claims were collapsed")
    if "red" in contradiction["visual"]["value"] or "blue" in contradiction["supplied_text"]["value"]:
        raise RuntimeError("contradictory claims were copied into each other")
    cases.append("text_image_contradiction")

    caption = "a toolbox on the desk"
    color = "blue"
    if color in caption:
        raise RuntimeError("caption already contains the missed color")
    missed = {"caption": {"value": caption, "origin": "supplied_caption"}, "color": color}
    if missed["color"] in missed["caption"]["value"]:
        raise RuntimeError("missed color was written into the caption")
    cases.append("caption_misses_color")

    digest = hashlib.sha256(PNG).hexdigest()
    asset = stable_id("media-asset-v1", "ns-a", digest)
    other_asset = stable_id("media-asset-v1", "ns-b", digest)
    first = stable_id("media-occ-v1", "source-1", 0)
    second = stable_id("media-occ-v1", "source-2", 0)
    if asset == other_asset or first == second or hashlib.sha256(PNG).hexdigest() != digest:
        raise RuntimeError("shared bytes did not keep separate occurrences and namespaces")
    cases.append("same_image_multiple_sources")

    near = PNG + b"\x01"
    if hashlib.sha256(PNG).hexdigest() == hashlib.sha256(near).hexdigest():
        raise RuntimeError("similar images hashed equal")
    if stable_id("media-asset-v1", "ns-a", hashlib.sha256(PNG).hexdigest()) == stable_id(
        "media-asset-v1", "ns-a", hashlib.sha256(near).hexdigest()
    ):
        raise RuntimeError("similar images merged into one asset")
    cases.append("similar_items_not_merged")
    return cases


def main() -> int:
    cases = verify()
    print(json.dumps({"ok": True, "cases": cases}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
