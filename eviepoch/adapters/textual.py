from __future__ import annotations

from ..domain import Source
from ..domain.requests import AddRequest
from .base import IngestExtension, TaskAdapter


class TextualAdapter(TaskAdapter):
    def normalize_add(self, request: AddRequest) -> AddRequest:
        for message in request.messages:
            if isinstance(message.content, list) or message.envelope is not None:
                raise ValueError("ordered content arrays require EPOCH_TASK_ADAPTER=multimodal")
        return request

    def extend_ingest(self, sources: list[Source], known, providers=None) -> IngestExtension:
        return IngestExtension()
