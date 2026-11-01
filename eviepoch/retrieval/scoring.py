"""Shared lexical relevance helpers for retrieval strategies."""
from __future__ import annotations

from .lexical import terms

def overlap(left: str, right: str) -> float:
    a, b = set(terms(left)), set(terms(right))
    return len(a & b) / max(1, len(a))
