"""AML ContentPart contract (TRD §20.1)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

ContentPart = dict[str, Any]

_DATA_URI = re.compile(
    r"^data:(image/(?:jpeg|jpg|png|webp));base64,([A-Za-z0-9+/=\s]+)$",
    re.I,
)
_TEXT_PART = "text"
_IMAGE_PART = "image_url"


@dataclass
class SourceEnvelope:
    """Ordered original parts; text is verbatim, images reference asset_id after ingest."""

    parts: list[ContentPart] = field(default_factory=list)

    def text_for_extraction(self) -> str:
        """Text-only surrogate for LLM extraction; never replaces stored envelope."""
        lines = []
        for part in self.parts:
            if part.get("type") == _TEXT_PART:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    lines.append(text)
        return "\n".join(lines)

    def has_media(self) -> bool:
        return any(part.get("type") == _IMAGE_PART for part in self.parts)

    def decoded_image_bytes(self) -> int:
        total = 0
        for part in self.parts:
            if part.get("type") != _IMAGE_PART:
                continue
            _, decoded = decode_data_uri(part["image_url"]["url"])
            total += len(decoded)
        return total

    def to_locator_dict(self, *, staging: bool = False) -> dict[str, Any]:
        slim = []
        for index, part in enumerate(self.parts):
            if part.get("type") == _TEXT_PART:
                slim.append({"type": _TEXT_PART, "part_ordinal": index, "text": part.get("text", "")})
            elif part.get("type") == _IMAGE_PART:
                url = (part.get("image_url") or {}).get("url", "")
                mime, raw = decode_data_uri(url) if url else ("", b"")
                item: dict[str, Any] = {
                    "type": _IMAGE_PART,
                    "part_ordinal": index,
                    "content_hash": hashlib.sha256(raw).hexdigest() if raw else "",
                    "mime": mime,
                    "byte_length": len(raw),
                    "asset_id": part.get("asset_id"),
                }
                if staging and url:
                    item["data_uri"] = url
                if part.get("asset_id"):
                    item["stored"] = True
                slim.append(item)
            else:
                slim.append({"type": part.get("type"), "part_ordinal": index})
        return {"schema": "source-envelope-v1", "parts": slim}


def normalize_content(value: str | list[ContentPart]) -> SourceEnvelope:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("content must not be blank")
        return SourceEnvelope([{"type": _TEXT_PART, "text": value}])
    if not isinstance(value, list) or not value:
        raise ValueError("content array must be non-empty")
    parts: list[ContentPart] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each content part must be an object")
        kind = item.get("type")
        if kind == _TEXT_PART:
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text part requires non-empty text")
            parts.append({"type": _TEXT_PART, "text": text})
        elif kind == _IMAGE_PART:
            url = (item.get("image_url") or {}).get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("image part requires image_url.url")
            decode_data_uri(url.strip())
            parts.append({"type": _IMAGE_PART, "image_url": {"url": url.strip()}})
        else:
            raise ValueError(f"unsupported content part type: {kind!r}")
    return SourceEnvelope(parts)


def decode_data_uri(url: str) -> tuple[str, bytes]:
    match = _DATA_URI.match(url.strip())
    if not match:
        raise ValueError("image must be a JPEG/PNG/WebP base64 data URI")
    mime = match.group(1).lower().replace("jpg", "jpeg")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image payload") from exc
    if not raw:
        raise ValueError("decoded image is empty")
    return mime, raw


def data_uri_from_bytes(raw: bytes, mime: str) -> str:
    if mime.lower().replace("jpg", "jpeg") not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("unsupported image mime")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def query_text(query: str | list[ContentPart]) -> str:
    if isinstance(query, str):
        return query
    envelope = normalize_content(query)
    text = envelope.text_for_extraction()
    if text.strip():
        return text
    return "[image query]"


def validate_media_limits(envelope: SourceEnvelope, *, max_single: int, max_total: int) -> None:
    total = 0
    for part in envelope.parts:
        if part.get("type") != _IMAGE_PART:
            continue
        _, raw = decode_data_uri(part["image_url"]["url"])
        if len(raw) > max_single:
            raise ValueError(f"image exceeds {max_single} decoded bytes")
        total += len(raw)
    if total > max_total:
        raise ValueError(f"media total exceeds {max_total} decoded bytes")
