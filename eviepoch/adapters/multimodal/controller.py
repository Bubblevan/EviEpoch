"""Multimodal internal search phases (TRD §20.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config import Settings
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...storage.view import View
from .search import hydrate_multimodal_evidence, multimodal_lexical_channel, query_image_channel


@dataclass
class MultimodalSearchState:
    lexical_hits: dict[str, float] = field(default_factory=dict)
    query_image_hits: dict[str, float] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)


def run_multimodal_search(view: View, request: SearchRequest, limit: int, config: Settings) -> MultimodalSearchState:
    state = MultimodalSearchState()
    with view.repo.view(view.ns) as db_view:
        state.lexical_hits = multimodal_lexical_channel(db_view, request.query, limit)
        state.query_image_hits = query_image_channel(db_view, request.query, limit)
    state.trace = {
        "multimodal_lexical": len(state.lexical_hits),
        "multimodal_query_image": len(state.query_image_hits),
    }
    return state


def apply_multimodal_evidence(view: View, state: MultimodalSearchState, evidence: list[Evidence],
                              config: Settings) -> list[Evidence]:
    _ = state
    with view.repo.view(view.ns) as db_view:
        return hydrate_multimodal_evidence(db_view, evidence, config)
