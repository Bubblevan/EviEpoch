"""Multimodal internal search phases (TRD §5, §6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config import Settings
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...storage.view import View
from .budget import MediaBudget
from .evidence_set import assess_change, bundle_change_evidence, is_change_question, order_change_evidence
from .search import (
    build_query_bundle,
    hydrate_multimodal_evidence,
    multimodal_lexical_channel,
    query_image_channel,
    returned_image_hashes,
)


@dataclass
class MultimodalSearchState:
    lexical_hits: dict[str, float] = field(default_factory=dict)
    query_image_hits: dict[str, float] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)


def _text_returned(evidence: list[Evidence]) -> bool:
    for item in evidence:
        content = item.content
        if isinstance(content, str) and content.strip():
            return True
        if isinstance(content, list) and any(
            part.get("type") == "text" and str(part.get("text") or "").strip() for part in content
        ):
            return True
    return False


def run_multimodal_search(view: View, request: SearchRequest, limit: int, config: Settings) -> MultimodalSearchState:
    bundle = build_query_bundle(request.query)
    state = MultimodalSearchState()
    with view.repo.view(view.ns) as db_view:
        state.lexical_hits = multimodal_lexical_channel(db_view, request.query, limit)
        state.query_image_hits = query_image_channel(db_view, request.query, limit)
    state.trace = {
        "multimodal_lexical": len(state.lexical_hits),
        "multimodal_query_image": len(state.query_image_hits),
        "visual_embedding": False,
        "query_bundle": bundle,
        "phases": ["search_index"],
        "obligations": [
            {"id": f"mm-visual-{name}", "kind": name} for name in bundle["visual_obligations"]
        ],
    }
    return state


def apply_multimodal_evidence(view: View, state: MultimodalSearchState, evidence: list[Evidence],
                              config: Settings, request: SearchRequest) -> list[Evidence]:
    phases = state.trace.setdefault("phases", [])
    if "search_index" not in phases:
        phases.append("search_index")
    include_images = config.multimodal_arm != "m0"
    budget = MediaBudget(config, request.query, request.options)
    with view.repo.view(view.ns) as db_view:
        prepared = order_change_evidence(db_view, evidence, state.lexical_hits, request.query)
        enriched = hydrate_multimodal_evidence(
            db_view, prepared, config, include_images=include_images, budget=budget,
        )
        report = assess_change(db_view, enriched, request.query) if is_change_question(request.query) else None
        hashes, matched = returned_image_hashes(db_view, enriched)
    phases.append("hydrate_original")

    obligations = list((state.trace.get("query_bundle") or {}).get("visual_obligations") or [])
    caption_hit = bool(obligations) and _text_returned(enriched)
    original_returned = bool(hashes)
    image_complete = bool(obligations) and config.multimodal_arm == "m1" and matched and original_returned
    status: dict[str, bool] = {}
    if report is None:
        complete = image_complete
        status = {f"mm-visual-{name}": complete for name in obligations}
        gaps = ["original_image"] if obligations and not complete else []
    else:
        for name in obligations:
            status[f"mm-visual-{name}"] = bool(report["admissible"])
        complete = bool(status) and all(status.values())
        gaps = list(report["gaps"])
        for item in budget.omitted:
            reason = item.get("reason")
            if reason and reason not in gaps and reason != "hash_mismatch":
                gaps.append(reason)
    state.trace.update({
        "caption_hit": caption_hit,
        "caption_completes_visual": False,
        "original_returned": original_returned,
        "returned_image_hashes": hashes,
        "hash_matches": matched,
        "visual_obligation_complete": complete,
        "obligation_status": status,
        "gaps": gaps,
        "budget": budget.to_trace(),
    })
    if report is not None:
        state.trace["evidence_set"] = report
        state.trace["comparison_complete"] = report["comparison_complete"]
    phases.append("compare_visual_support")
    counter = [{"kind": "caption_not_visual_proof"}] if obligations and caption_hit else []
    if report is not None and not report["admissible"]:
        counter.append({"kind": "change_comparison_incomplete"})
        if report["single_image_is_not_a_comparison"]:
            counter.append({"kind": "later_image_is_not_a_completed_comparison"})
    state.trace["counterevidence"] = counter
    phases.append("find_counterevidence")
    if report is not None:
        enriched = bundle_change_evidence(enriched, report, request.top_k)
    phases.append("select")
    return enriched
