"""TaskAdapter contract (TRD §18.2)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..domain import Edge, Node, QueryPlan, Source, Span
from ..domain.requests import AddRequest, SearchRequest
from ..retrieval.evidence import Evidence
from ..storage.view import View


@dataclass
class CodingArtifact:
    """Persisted engineering object stored in ee_objects with kind coding_*."""

    id: str
    kind: str
    payload: dict[str, Any]
    body: str
    source_ids: list[str]
    asserted_at: str | None = None
    vector_text: str | None = None


@dataclass
class IngestExtension:
    spans: list[Span] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    artifacts: list[CodingArtifact] = field(default_factory=list)


@dataclass
class SearchExtension:
    extra_channels: list[dict[str, float]] = field(default_factory=list)
    channel_names: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


class TaskAdapter(ABC):
    def __init__(self, config: Settings):
        self.config = config

    def normalize_add(self, request: AddRequest) -> AddRequest:
        return request

    @abstractmethod
    def extend_ingest(self, sources: list[Source], known, providers=None) -> IngestExtension:
        ...

    def extend_plan(self, plan: QueryPlan, query: str | list) -> QueryPlan:
        return plan

    def extend_retrieval(self, view: View, request: SearchRequest, plan: QueryPlan) -> SearchExtension:
        return SearchExtension()

    def extend_evidence(self, view: View, request: SearchRequest, plan: QueryPlan,
                        evidence: list[Evidence]) -> tuple[list[Evidence], dict[str, Any]]:
        return evidence, {}
