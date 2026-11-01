"""Source-backed proof construction, binding, and safe fallback."""
from __future__ import annotations

from dataclasses import replace

from ..domain import InvalidExtraction, Obligation, QueryPlan, Snapshot
from ..providers.errors import ProviderUnavailable
from .evidence import Evidence
from .scoring import overlap
from .selection import select

def _time_weight(node, plan: QueryPlan, snap: Snapshot) -> float:
    historical = bool(set(plan.intents) & {"as_of", "history", "comparison", "order"})
    if historical:
        if plan.as_of.latest:
            lower = plan.as_of.earliest or plan.as_of.latest
            if (node.valid_time.earliest and node.valid_time.earliest > plan.as_of.latest
                    or node.valid_time.latest and node.valid_time.latest < lower):
                return -0.5
            if (node.valid_time.earliest and node.valid_time.earliest <= plan.as_of.latest
                    and (not node.valid_time.latest or node.valid_time.latest >= lower)):
                return 0.3
        return 0.0
    if "current" not in plan.intents:
        return 0.0
    if node.modality in {"planned", "hypothetical"}:
        return -0.5
    withdrawn = any(e.target == node.id and e.kind in {"CORRECTS", "RETRACTS", "SUPERSEDES"}
                    for e in snap.edges)
    return -0.4 if withdrawn else 0.3


def _closure(node_id: str, plan: QueryPlan, snap: Snapshot, depth: int) -> set[str]:
    visited, frontier = set(), {node_id}
    path_sources = set()
    for _ in range(depth + 1):
        next_ids = set()
        for oid in frontier - visited:
            visited.add(oid)
            for edge in snap.edges:
                # Dependent -> prerequisite/exception. Cause -> effect. Keep true direction.
                if edge.origin == oid and edge.kind in {"REQUIRES", "EXCEPT_IF", "RULE_VERSION"}:
                    if "procedure" in plan.intents or edge.kind in plan.required_relations:
                        next_ids.add(edge.target)
                        path_sources.update(edge.source_ids)
                if edge.target == oid and edge.kind in {"CAUSE_STATED", "ENABLES", "RESULT_OF"}:
                    if "why" in plan.intents or edge.kind in plan.required_relations:
                        next_ids.add(edge.origin)
                        path_sources.update(edge.source_ids)
                if edge.target == oid and edge.kind in {"CORRECTS", "RETRACTS"}:
                    next_ids.add(edge.origin)
                    path_sources.update(edge.source_ids)
                if edge.kind == "CONTRADICTS" and oid in {edge.origin, edge.target}:
                    next_ids.update((edge.origin, edge.target))
                    path_sources.update(edge.source_ids)
                if edge.kind in {"SUPERSEDES", "BEFORE"} and oid in {edge.origin, edge.target}:
                    if set(plan.intents) & {"history", "comparison", "order"}:
                        next_ids.update((edge.origin, edge.target))
                        path_sources.update(edge.source_ids)
        frontier = next_ids - visited
        if not frontier:
            break
    return path_sources | {sid for oid in visited if oid in snap.nodes for sid in snap.nodes[oid].source_ids}


def proofs(plan: QueryPlan, snap: Snapshot, scores: dict[str, float], config,
           candidate_sources: set[str] | None = None) -> QueryPlan:
    for obligation in plan.obligations:
        ranked = []
        for node in snap.nodes.values():
            if node.kind in {"summary", "episode"}:
                continue
            if candidate_sources is not None and (
                    not node.source_ids or not set(node.source_ids) <= candidate_sources):
                continue
            lexical = overlap(obligation.query, node.representation())
            if lexical <= 0:
                continue
            value = lexical + _time_weight(node, plan, snap) + scores.get(node.id, 0)
            ranked.append((value, node))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
        if ranked:
            best = ranked[0][0]
            for value, node in ranked[:6]:
                if value < max(0.05, best - 0.18):
                    continue
                group = (_closure(node.id, plan, snap, config.graph_depth) if config.relations_enabled
                         else set(node.source_ids))
                if group and group <= snap.sources.keys():
                    obligation.alternatives.append(sorted(group))
        if not obligation.alternatives:
            # Dense-only matches can still provide source evidence, but never imply verified relations.
            candidates = [(scores.get(s.id, 0) + overlap(obligation.query, s.content), s.id)
                          for s in snap.sources.values()]
            candidates.sort(key=lambda row: (-row[0], row[1]))
            for score, sid in candidates[:3]:
                if score > 0:
                    obligation.alternatives.append([sid])
        obligation.alternatives = [list(g) for g in dict.fromkeys(tuple(g) for g in obligation.alternatives)]
    return plan


def _proof_candidate_sources(plan: QueryPlan, snap: Snapshot, scores: dict[str, float], config) -> set[str]:
    """Find bounded local proof candidates across the whole snapshot for a later rerank pass."""
    result = set()
    for obligation in plan.obligations:
        ranked = []
        for node in snap.nodes.values():
            if node.kind in {"summary", "episode"}:
                continue
            lexical = overlap(obligation.query, node.representation())
            if lexical > 0:
                ranked.append((lexical + _time_weight(node, plan, snap) + scores.get(node.id, 0), node))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
        candidates = []
        if ranked:
            best = ranked[0][0]
            candidates = [node for value, node in ranked[:6] if value >= max(0.05, best - 0.18)]
        for node in candidates:
            group = (_closure(node.id, plan, snap, config.graph_depth) if config.relations_enabled
                     else set(node.source_ids))
            result.update(group & snap.sources.keys())
        if not candidates:
            raw = [(scores.get(source.id, 0) + overlap(obligation.query, source.content), source.id)
                   for source in snap.sources.values()]
            raw.sort(key=lambda row: (-row[0], row[1]))
            result.update(sid for score, sid in raw[:3] if score > 0)
    return result


def bind_proofs(plan, snap, query, providers, config):
    data = providers.bind_obligations(query, plan, snap)
    if not isinstance(data, dict):
        raise InvalidExtraction("proof binding must be an object")
    rows = data.get("obligations")
    if not isinstance(rows, list):
        raise InvalidExtraction("proof binding requires an obligations array")
    expected = {o.id: o for o in plan.obligations}
    found = set()
    staged = {}
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidExtraction("bound obligation must be an object")
        oid, groups = row.get("id"), row.get("alternatives")
        if not isinstance(oid, str) or oid not in expected or oid in found or not isinstance(groups, list) or len(groups) > 8:
            raise InvalidExtraction("invalid bound obligation")
        found.add(oid)
        alternatives = []
        for group in groups:
            if not isinstance(group, list) or not group or len(group) > 16:
                raise InvalidExtraction("invalid AND evidence set")
            refs = set()
            for key in group:
                if not isinstance(key, str):
                    raise InvalidExtraction("evidence IDs must be strings")
                if key in snap.nodes:
                    refs.update(_closure(key, plan, snap, config.graph_depth) if config.relations_enabled
                                else snap.nodes[key].source_ids)
                elif key in snap.sources:
                    refs.add(key)
                else:
                    raise InvalidExtraction("proof binding invented an evidence ID")
            if refs <= snap.sources.keys():
                alternatives.append(sorted(refs))
        staged[oid] = alternatives
    if found != expected.keys():
        raise InvalidExtraction("proof binding omitted an obligation")
    preferred = data.get("preferred_sources", [])
    if (not isinstance(preferred, list) or len(preferred) > 8
            or any(not isinstance(sid, str) or sid not in snap.sources for sid in preferred)):
        raise InvalidExtraction("invalid preferred proof sources")
    for oid, alternatives in staged.items():
        expected[oid].alternatives = alternatives
    if preferred:
        group = [sorted(set(preferred))]
        if "direct_support" in expected:
            expected["direct_support"].alternatives = group
        else:
            plan.obligations.append(Obligation("direct_support", query, alternatives=group))
    return plan


def _fallback_sources(snap, plan):
    """Only disclose live snapshot sources; exclude clearly inapplicable time/modal states."""
    result = {}
    for sid, source in snap.sources.items():
        nodes = [n for n in snap.nodes.values() if sid in n.source_ids and n.kind not in {"summary", "episode"}]
        if nodes and "current" in plan.intents and all(n.modality in {"planned", "hypothetical"} for n in nodes):
            continue
        if nodes and plan.as_of.latest and all(n.valid_time.earliest and n.valid_time.earliest > plan.as_of.latest
                                               for n in nodes):
            continue
        result[sid] = source
    return result


def controlled_fallback(request, plan, snap, evidence, providers, config, budget):
    live = _fallback_sources(snap, plan)
    if not live:
        return [], "no_support_in_candidates"
    audit = getattr(providers, "audit_evidence", None)
    if audit is None:
        raise ProviderUnavailable("proof selection failed and no independent evidence audit is available")
    # Audit the actual evidence unit that can be returned. Approval of a large source
    # must not be reused as proof that an arbitrary short span supports the question.
    audit_units = {eid: replace(live[e.source_id], id=eid, content=e.content)
                   for eid, e in evidence.items() if e.source_id in live}
    if not audit_units:
        return [], "no_support_in_candidates"
    result = audit(request.query, plan, audit_units)
    if not isinstance(result, dict):
        raise ProviderUnavailable("evidence audit must be an object")
    ids, status = result.get("ids"), result.get("status")
    if (not isinstance(ids, list) or status not in {"relevant", "insufficient", "uncertain"}
            or any(not isinstance(sid, str) or sid not in audit_units for sid in ids)
            or bool(ids) != (status in {"relevant", "uncertain"})):
        raise ProviderUnavailable("evidence audit returned invalid or uncertain support")
    if not ids:
        return [], "no_support_in_candidates"
    allowed = {eid: e for eid, e in evidence.items() if eid in ids}
    # This is explicitly partial evidence. It does not turn an unfinished AND proof into a full proof.
    # The audit establishes that these evidence units are relevant. Let the normal
    # proof-aware selector retain useful partial AND support among the approved IDs.
    selected = select(plan, allowed, config, request.top_k, budget, "beam", pack_output=False)
    return selected, ("uncertain_fallback" if status == "uncertain" else "verified_fallback") if selected else "no_evidence_fits_budget"
