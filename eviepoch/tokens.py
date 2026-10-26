from __future__ import annotations

import math
from functools import lru_cache

from .config import Settings

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


@lru_cache(maxsize=4096)
def count(text: str) -> int:
    if not text:
        return 0
    encoding = _get_encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    total = 0.0
    for char in text:
        if char.isascii():
            total += 1 / 3 if char.isalpha() else 0.5 if char.isdigit() else 0.25 if char.isspace() else 1
        else:
            total += len(char.encode("utf-8")) / 3
    return math.ceil(total) + 1


def budget(config: Settings, query: str, options: list[str] | None) -> int:
    fixed = count(query)
    if options:
        fixed += sum(count(str(option)) + 2 for option in options)
    available = (config.answer_input_tokens - config.answer_prompt_tokens) / max(config.token_safety, 1.0)
    return int(available) - fixed


def item_cost(config: Settings, text: str) -> int:
    return count(text) + config.answer_item_tokens
