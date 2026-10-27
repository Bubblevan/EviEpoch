from __future__ import annotations

import math
import json

from ..config import Settings

_encoding = None
_loaded = False


def _get_encoding():
    global _encoding, _loaded
    if not _loaded:
        try:
            import tiktoken
            _encoding = tiktoken.get_encoding("o200k_base")
        except Exception:
            _encoding = None
        _loaded = True
    return _encoding


def count(text: str) -> int:
    if not text:
        return 0
    encoding = _get_encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    return len(text.encode("utf-8"))


def budget(config: Settings, query: str, options: list[str] | None) -> int:
    fixed = count(query)
    if options:
        fixed += sum(count(str(option)) + 2 for option in options)
    available = (config.answer_input_tokens - config.answer_prompt_tokens) / max(config.token_safety, 1.0)
    return int(available) - fixed


def item_cost(config: Settings, text: str) -> int:
    return count(text) + config.answer_item_tokens


def evidence_cost(config: Settings, item) -> int:
    payload = {"id": item.id, "content": item.content, "score": float(item.score)}
    if item.created_at:
        payload["created_at"] = item.created_at
    return count(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) + config.answer_item_tokens
