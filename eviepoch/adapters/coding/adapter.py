from __future__ import annotations

from ...domain import QueryPlan, Source
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...storage.view import View
from ..base import IngestExtension, SearchExtension, TaskAdapter
from .controller import apply_coding_evidence, run_coding_search
from .ingest import ingest_sources
from .search import applicability_obligations, plan_obligations, restore_requested_analogy


class CodingAdapter(TaskAdapter):
    def __init__(self, config):
        super().__init__(config)
        self._search_state = None

    def extend_ingest(self, sources: list[Source], known, providers=None) -> IngestExtension:
        return ingest_sources(sources, providers)

    def extend_plan(self, plan: QueryPlan, query: str) -> QueryPlan:
        return plan_obligations(plan, query)

    def extend_retrieval(self, view: View, request: SearchRequest, plan: QueryPlan) -> SearchExtension:
        self._search_state = run_coding_search(view, request, self.config.candidate_limit)
        state = self._search_state
        if getattr(view, "c", None) is None and getattr(view, "repo", None) is not None:
            with view.repo.view(view.ns) as db_view:
                plan.obligations.extend(applicability_obligations(db_view, state.assessments))
        else:
            plan.obligations.extend(applicability_obligations(view, state.assessments))
        channels = []
        names = []
        if state.index_hits:
            channels.append(state.index_hits)
            names.append("coding_index")
        if state.timeline_hits:
            channels.append(state.timeline_hits)
            names.append("coding_timeline")
        if state.version_hits:
            channels.append(state.version_hits)
            names.append("coding_version")
        if state.analogy_hits:
            channels.append(state.analogy_hits)
            names.append("coding_analogy")
        if state.graph_hits:
            channels.append(state.graph_hits)
            names.append("coding_graph")
        if state.promoted_source_hits:
            channels.append(state.promoted_source_hits)
            names.append("coding_sources")
        return SearchExtension(extra_channels=channels, channel_names=names, trace=state.trace)

    def extend_evidence(self, view: View, request: SearchRequest, plan: QueryPlan,
                        evidence: list[Evidence]) -> tuple[list[Evidence], dict]:
        state = self._search_state or run_coding_search(view, request, self.config.candidate_limit)
        self._search_state = None
        evidence = restore_requested_analogy(view, request.query_text, evidence, state.analogy_hits)
        enriched = apply_coding_evidence(
            view, state, evidence, request.query_text, plan, self.config, request.top_k,
        )
        trace = {"coding_applicability": [item.to_dict() for item in state.assessments], **state.trace}
        return enriched, trace
