from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .content import ContentPart, SourceEnvelope, normalize_content, query_text


@dataclass
class Message:
    role: str
    content: str
    timestamp: int | None = None
    envelope: SourceEnvelope | None = field(default=None, repr=False)


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
    query: str | list[ContentPart]
    user_id: str
    top_k: int
    options: list[str] | None = None

    @property
    def query_text(self) -> str:
        return query_text(self.query)


def add_payload_digest(request: AddRequest) -> str:
    from .models import digest

    messages = []
    for message in request.messages:
        if message.envelope is not None:
            messages.append(
                {
                    "role": message.role,
                    "timestamp": message.timestamp,
                    "content": message.envelope.to_locator_dict(staging=True),
                }
            )
        else:
            messages.append({"role": message.role, "timestamp": message.timestamp, "content": message.content})
    return digest(
        {
            "request_id": request.request_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "messages": messages,
        }
    )
