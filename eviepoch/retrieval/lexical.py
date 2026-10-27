"""Versioned English words + CJK characters/bigrams; statistics are namespace local."""
from __future__ import annotations

import re
from collections import Counter

TOKENIZER_VERSION = "words-cjk-bigrams-v1"
STOP = frozenset("a an the i me my you your is are was were am do did does what which where when how why of in on at to and or for with have has had now currently please tell about it that this".split())


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    result = []
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff":
            result.extend(word)
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
        elif word not in STOP:
            result.append(word)
    return result


def frequencies(text: str) -> Counter:
    return Counter(terms(text))
