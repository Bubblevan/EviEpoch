from __future__ import annotations

from ..domain import Source
from .base import IngestExtension, TaskAdapter


class TextualAdapter(TaskAdapter):
    def extend_ingest(self, sources: list[Source], known, providers=None) -> IngestExtension:
        return IngestExtension()
