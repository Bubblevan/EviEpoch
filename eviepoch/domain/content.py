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


def normalize_content(
    value: str | list[ContentPart],
    *,
    max_single: int | None = None,
    max_total: int | None = None,
) -> SourceEnvelope:
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
            parts.append({"type": _IMAGE_PART, "image_url": {"url": url.strip()}})
        else:
            raise ValueError(f"unsupported content part type: {kind!r}")
    urls = image_urls(parts)
    if max_single is not None or max_total is not None:
        precheck_data_uris(
            urls,
            max_single=max_single if max_single is not None else max_total or 0,
            max_total=max_total if max_total is not None else max_single or 0,
        )
    for url in urls:
        decode_data_uri(url, max_bytes=max_single)
    return SourceEnvelope(parts)


def _split_data_uri(url: str) -> tuple[str, str]:
    match = _DATA_URI.match(url.strip())
    if not match:
        raise ValueError("image must be a JPEG/PNG/WebP base64 data URI")
    mime = match.group(1).lower().replace("jpg", "jpeg")
    payload = "".join(match.group(2).split())
    return mime, payload


def estimated_decoded_bytes(url: str) -> int:
    """Decoded size from the Base64 length. Does not allocate the image buffer."""
    _mime, payload = _split_data_uri(url)
    if not payload or len(payload) % 4 != 0:
        raise ValueError("invalid base64 image payload")
    padding = len(payload) - len(payload.rstrip("="))
    if padding > 2:
        raise ValueError("invalid base64 image payload")
    size = (len(payload) * 3) // 4 - padding
    if size <= 0:
        raise ValueError("decoded image is empty")
    return size


def _magic_matches(mime: str, raw: bytes) -> bool:
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    return False


def image_urls(value: str | list[ContentPart]) -> list[str]:
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") != _IMAGE_PART:
            continue
        url = (item.get("image_url") or {}).get("url")
        if isinstance(url, str) and url.strip():
            found.append(url.strip())
    return found


def precheck_data_uris(urls: list[str], *, max_single: int, max_total: int) -> None:
    total = 0
    for url in urls:
        size = estimated_decoded_bytes(url)
        if size > max_single:
            raise ValueError(f"image exceeds {max_single} decoded bytes")
        total += size
        if total > max_total:
            raise ValueError(f"media total exceeds {max_total} decoded bytes")


def decode_data_uri(url: str, *, max_bytes: int | None = None) -> tuple[str, bytes]:
    mime, payload = _split_data_uri(url)
    size = estimated_decoded_bytes(url)
    if max_bytes is not None and size > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} decoded bytes")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image payload") from exc
    if not raw:
        raise ValueError("decoded image is empty")
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} decoded bytes")
    if not _magic_matches(mime, raw):
        raise ValueError("image bytes do not match the declared MIME type")
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
    urls = []
    for part in envelope.parts:
        if part.get("type") != _IMAGE_PART:
            continue
        url = (part.get("image_url") or {}).get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("image part requires image_url.url")
        urls.append(url.strip())
    precheck_data_uris(urls, max_single=max_single, max_total=max_total)
    for url in urls:
        decode_data_uri(url, max_bytes=max_single)
