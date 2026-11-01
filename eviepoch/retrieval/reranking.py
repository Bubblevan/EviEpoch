"""Cross-encoder candidate pooling and score projection into proof search."""
from __future__ import annotations

from ..domain import QueryPlan, Snapshot
from .graph import _source_ids_for_objects
from .proofs import _closure, _time_weight
from .scoring import overlap

def _bounded_rerank_ids(snap: Snapshot, source_scores: dict[str, float], fused: dict[str, float],
                        temporal_candidates, query: str, config,
                        scope: set[str] | None = None) -> list[str]:
    """Choose a bounded, stratified pool while leaving every other source recoverable."""
    eligible = set(snap.sources) if scope is None else set(scope) & set(snap.sources)
    if not eligible:
        return []
    limit = min(config.rerank_candidate_limit, len(eligible))
    quota = min(config.rerank_category_quota, max(1, limit // 4))
    categories = {"source": set(), "temporal": set(), "rule": set(), "counterevidence": set()}
    categories["source"].update(_source_ids_for_objects(fused.keys(), snap))
    categories["source"].update(
        sid for node in snap.nodes.values()
        if overlap(query, node.representation()) > 0 for sid in node.source_ids)
    categories["temporal"].update(_source_ids_for_objects(temporal_candidates, snap))
    categories["temporal"].update(
        sid for node in snap.nodes.values()
        if (node.valid_time.earliest or node.valid_time.latest
            or node.occurrence_time.earliest or node.occurrence_time.latest)
        for sid in node.source_ids)
    rule_kinds = {"REQUIRES", "EXCEPT_IF", "RULE_VERSION"}
    counter_kinds = {"CONTRADICTS", "CORRECTS", "RETRACTS", "SUPERSEDES"}
    categories["rule"].update(
        sid for node in snap.nodes.values() if node.kind in {"rule", "procedure"}
        for sid in node.source_ids)
    for node in snap.nodes.values():
        if node.counterexample_ids or node.state == "challenged":
            categories["counterevidence"].update(node.source_ids)
            categories["counterevidence"].update(
                sid for oid in node.counterexample_ids if oid in snap.nodes
                for sid in snap.nodes[oid].source_ids)
    for edge in snap.edges:
        related_sources = set(edge.source_ids)
        for nid in (edge.origin, edge.target):
            if nid in snap.nodes:
                related_sources.update(snap.nodes[nid].source_ids)
        if edge.kind in rule_kinds:
            categories["rule"].update(related_sources)
        if edge.kind in counter_kinds:
            categories["counterevidence"].update(related_sources)
    ranked = sorted(eligible, key=lambda sid: (-source_scores.get(sid, 0.0), sid))
    chosen = []
    chosen_set = set()
    for name in ("source", "temporal", "rule", "counterevidence"):
        ids = categories[name] & eligible
        already = len(ids & chosen_set)
        ordered = sorted(ids, key=lambda sid: (-source_scores.get(sid, 0.0), sid))
        for sid in ordered:
            if already >= quota or len(chosen) >= limit:
                break
            if sid not in chosen_set:
                chosen.append(sid)
                chosen_set.add(sid)
            already += 1
    for sid in ranked:
        if len(chosen) >= limit:
            break
        if sid not in chosen_set:
            chosen.append(sid)
            chosen_set.add(sid)
    return chosen


def _proof_score_map(snap: Snapshot, source_scores: dict[str, float],
                     candidate_sources: set[str]) -> dict[str, float]:
    values = [source_scores[sid] for sid in candidate_sources if sid in source_scores]
    if not values:
        return {}
    low, high = min(values), max(values)
    scale = high - low
    normalized = {sid: ((source_scores[sid] - low) / scale if scale > 1e-12 else 0.5)
                  for sid in candidate_sources if sid in source_scores}
    result = {}
    for node in snap.nodes.values():
        if node.source_ids and set(node.source_ids) <= candidate_sources:
            result[node.id] = max(normalized[sid] for sid in node.source_ids)
    return result
