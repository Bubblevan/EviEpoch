from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Message:
    role: str
    content: str
    timestamp: int | None = None


@dataclass
class Memory:
    id: str
    user_id: str
    session_id: str
    request_id: str
    kind: str
    chunk_id: str
    content: str
    created_at: str | None
    position: int
    vector: np.ndarray


@dataclass
class AddRequest:
    request_id: str
    user_id: str
    session_id: str
    messages: list[Message]


@dataclass
class SearchRequest:
    query: str
    user_id: str
    top_k: int
    options: list[str] | None = None
