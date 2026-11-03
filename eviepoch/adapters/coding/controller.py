"""
AML single-shot Search controller (TRD §19.5):
search_index → expand_attempt_timeline → hydrate_exact_evidence → compare_applicability → find_counterexample → select
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...retrieval.fusion import merge
from ...storage.view import View
from .timeline import build_timeline, filter_by_depth
from .search import (
    admissibility,
    coding_graph_channel,
    coding_lexical_channel,
    compare_applicability,
    find_counterexamples,
    hydrate_candidate_texts,
    hydrate_exact_evidence,
    promote_coding_hits,
    select_typed_evidence,
    select_versioned_evidence,
    version_channels,
    version_hypothesis,
    _connection,
    _load_payload,
    _source_ids,
)

# causal-memory fused.rs leg depth (Apache-2.0 behavior capsule)
FUSED_LEG_DEPTH = 50
FUSED_MAX_DEPTH = 200


@dataclass
class CodingSearchState:
    index_hits: dict[str, float] = field(default_factory=dict)
    timeline_hits: dict[str, float] = field(default_factory=dict)
    version_hits: dict[str, float] = field(default_factory=dict)
    analogy_hits: dict[str, float] = field(default_factory=dict)
    graph_hits: dict[str, float] = field(default_factory=dict)
    promoted_source_hits: dict[str, float] = field(default_factory=dict)
    assessments: list[Any] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)
    counterexample_sources: list[str] = field(default_factory=list)
    hydrated_texts: dict[str, str] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


def _epoch_from_payload(payload: dict[str, Any]) -> int:
    for key in ("event_time", "asserted_at", "stored_at"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return 0


def expand_attempt_timeline(view: View, index_hits: dict[str, float], *, depth_before: int = 3, depth_after: int = 5) -> dict[str, float]:
    observations = []
    for oid, score in sorted(index_hits.items(), key=lambda item: (-item[1], item[0]))[:12]:
        payload = _load_payload(view, oid)
        if not payload:
            continue
        observations.append({"id": oid, "created_at_epoch": _epoch_from_payload(payload), "score": score})
    if not observations:
        return {}
    anchor = observations[0]
    items = build_timeline(observations)
    window = filter_by_depth(items, anchor["id"], anchor["created_at_epoch"], depth_before, depth_after)
    scores: dict[str, float] = {}
    for rank, item in enumerate(window):
        oid = str(item.data.get("id"))
        scores[oid] = max(scores.get(oid, 0.0), 1.0 / (10 + rank))
    for oid in list(index_hits)[:12]:
        payload = _load_payload(view, oid)
        for related in payload.get("related_event_ids") or []:
            if isinstance(related, str) and related:
                scores[related] = max(scores.get(related, 0.0), 0.4)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            continue
        rows = _connection(view).execute(
            "SELECT id FROM ee_objects WHERE ns=? AND active>0 AND kind='coding_engineering_event' "
            "AND payload LIKE ? ORDER BY generation DESC LIMIT 8",
            (view.ns, f'%"session_id": "{session_id}"%'),
        ).fetchall()
        for (related_id,) in rows:
            scores[related_id] = max(scores.get(related_id, 0.0), 0.35)
    return scores


def run_coding_search(view: View, request: SearchRequest, limit: int) -> CodingSearchState:
    depth = min(max(limit, FUSED_LEG_DEPTH), FUSED_MAX_DEPTH)
    state = CodingSearchState()
    with view.repo.view(view.ns) as db_view:
        state.index_hits = coding_lexical_channel(db_view, request.query_text, depth)
        state.version_hits, state.analogy_hits, relations = version_channels(db_view, request.query_text, depth)
        timeline_input = {**state.index_hits, **state.version_hits}
        state.timeline_hits = expand_attempt_timeline(db_view, timeline_input)
        state.graph_hits = coding_graph_channel(db_view, list(timeline_input))
        merged, ranks = merge(
            [state.index_hits, state.timeline_hits, state.version_hits, state.analogy_hits, state.graph_hits],
            cap=depth,
            names=["coding_index", "coding_timeline", "coding_version", "coding_analogy", "coding_graph"],
        )
        state.promoted_source_hits = promote_coding_hits(db_view, merged)
        state.candidate_ids = list(merged.keys())
        attempt_ids = [oid for oid in merged if "patch" in oid or oid.startswith("coding-")]
        state.hydrated_texts = hydrate_candidate_texts(db_view, state.candidate_ids)
        state.assessments = compare_applicability(db_view, request.query_text, state.candidate_ids)
        state.counterexamples = find_counterexamples(db_view, attempt_ids)
        for experience_id in state.counterexamples:
            state.counterexample_sources.extend(_source_ids(db_view, experience_id))
        state.trace = {
            "controller": [
                "search_index",
                "expand_attempt_timeline",
                "hydrate_exact_evidence",
                "compare_applicability",
                "find_counterexample",
                "select",
            ],
            "channel_ranks": ranks,
            "counterexamples": state.counterexamples,
            "version_relations": relations,
            "admissible": admissibility(state.assessments, request.query_text),
            "hypothesis": version_hypothesis(db_view, request.query_text, state.assessments),
        }
    return state


def apply_coding_evidence(view: View, state: CodingSearchState, evidence: list[Evidence], query: str = "",
                          plan=None, config=None, top_k: int = 8) -> list[Evidence]:
    hydrated = hydrate_exact_evidence(view, evidence)
    filtered = select_versioned_evidence(view, query, hydrated, state.assessments)
    if plan is None or config is None:
        return filtered
    chosen, report = select_typed_evidence(
        view, filtered, state.assessments, plan, config, top_k, state.promoted_source_hits, query,
        state.counterexample_sources,
    )
    state.trace["typed_selection"] = report
    return chosen
