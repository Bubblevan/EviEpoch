"""Response media budget. Images are kept whole or omitted (TRD §6)."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from ...config import Settings
from ...domain.content import data_uri_from_bytes, decode_data_uri, query_text
from ...retrieval.tokens import budget as answer_budget
from ...retrieval.tokens import count


def keep_whole_image(url: str, asset: dict[str, Any]) -> str | None:
    """A response image is the canonical data URI of the full asset, or it is absent."""
    try:
        mime, raw = decode_data_uri(url)
    except ValueError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    if digest != asset.get("content_hash") or len(raw) != asset.get("byte_length"):
        return None
    canonical = data_uri_from_bytes(raw, str(asset.get("mime") or mime))
    if url != canonical:
        return None
    return url


def image_token_estimate(width: int | None, height: int | None) -> int:
    """16-pixel patch estimate. Unknown size is not treated as free."""
    if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
        return 256
    return max(1, math.ceil(width / 16) * math.ceil(height / 16))


class MediaBudget:
    def __init__(self, config: Settings, query: str | list, options: list[str] | None):
        self.config = config
        self.decoded = 0
        self.appearances = 0
        self.wire_bytes = 0
        self.image_tokens = 0
        self.text_tokens = 0
        self.omitted: list[dict[str, Any]] = []
        self.answer_limit = max(0, answer_budget(config, query_text(query) if query else "", options))

    def note_text(self, text: str) -> None:
        self.text_tokens += count(text)
        self.wire_bytes += len(json.dumps(text, ensure_ascii=False).encode("utf-8"))

    def allow_image(self, raw: bytes, mime: str, asset: dict[str, Any]) -> str | None:
        """Return a complete data URI, or omit the image. Never a sliced URI."""
        digest = hashlib.sha256(raw).hexdigest() if raw else ""
        expected = asset.get("content_hash")
        if not raw or digest != expected or len(raw) != asset.get("byte_length"):
            self.omitted.append({"reason": "hash_mismatch", "content_hash": expected})
            return None
        try:
            url = data_uri_from_bytes(raw, mime)
            declared, decoded = decode_data_uri(url)
        except ValueError:
            self.omitted.append({"reason": "undecodable", "content_hash": digest})
            return None
        if decoded != raw or declared != mime or url != data_uri_from_bytes(decoded, declared):
            self.omitted.append({"reason": "truncated_base64", "content_hash": digest})
            return None
        if len(raw) > self.config.multimodal_max_image_bytes:
            self.omitted.append({"reason": "image_too_large", "content_hash": digest, "decoded_bytes": len(raw)})
            return None
        if self.decoded + len(raw) > self.config.multimodal_max_response_bytes:
            self.omitted.append({
                "reason": "response_media_budget",
                "content_hash": digest,
                "decoded_bytes": len(raw),
            })
            return None
        estimate = image_token_estimate(asset.get("width"), asset.get("height"))
        if self.text_tokens + self.image_tokens + estimate > self.answer_limit:
            self.omitted.append({
                "reason": "answer_input_budget",
                "content_hash": digest,
                "image_tokens": estimate,
            })
            return None
        if keep_whole_image(url, asset) != url:
            self.omitted.append({"reason": "truncated_base64", "content_hash": digest})
            return None
        self.decoded += len(raw)
        self.appearances += 1
        self.image_tokens += estimate
        part = {"type": "image_url", "image_url": {"url": url}}
        self.wire_bytes += len(json.dumps(part, ensure_ascii=False).encode("utf-8"))
        return url

    def to_trace(self) -> dict[str, Any]:
        return {
            "decoded_image_bytes": self.decoded,
            "image_appearances": self.appearances,
            "max_image_bytes": self.config.multimodal_max_image_bytes,
            "max_response_bytes": self.config.multimodal_max_response_bytes,
            "wire_bytes": self.wire_bytes,
            "answer_image_tokens": self.image_tokens,
            "answer_text_tokens": self.text_tokens,
            "answer_budget": self.answer_limit,
            "image_token_estimate": "patch-16",
            "omitted": list(self.omitted),
        }
