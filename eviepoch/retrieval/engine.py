"""Hybrid discovery, query-conditioned proof bundles and bounded beam selection."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from . import tokens
from ..domain import InvalidExtraction, Obligation, QueryPlan, Snapshot
from .lexical import terms
from .fusion import merge

RETRIEVAL_VERSION = "rrf-proof-beam-v1"


@dataclass
class Evidence:
    id: str
    content: str
    created_at: str | None
    score: float
    source_id: str


def fuse(channels: list[dict[str, float]], k: int = 60) -> dict[str, float]:
    return merge(channels, k)[0]


def overlap(left: str, right: str) -> float:
    a, b = set(terms(left)), set(terms(right))
    return len(a & b) / max(1, len(a))


def _time_weight(node, plan: QueryPlan, snap: Snapshot) -> float:
    historical = bool(set(plan.intents) & {"as_of", "history", "comparison", "order"})
    if historical:
        if plan.as_of.latest and node.valid_time.earliest:
            if node.valid_time.earliest > plan.as_of.latest:
                return -0.5
            if node.valid_time.latest and node.valid_time.latest > plan.as_of.earliest:
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


def proofs(plan: QueryPlan, snap: Snapshot, scores: dict[str, float], config) -> QueryPlan:
    for obligation in plan.obligations:
        ranked = []
        for node in snap.nodes.values():
            if node.kind == "summary":
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


def bind_proofs(plan, snap, query, providers, config):
    data = providers.bind_obligations(query, plan, snap)
    rows = data.get("obligations")
    if not isinstance(rows, list):
        raise InvalidExtraction("proof binding requires an obligations array")
    expected = {o.id: o for o in plan.obligations}
    found = set()
    for row in rows:
        oid, groups = row.get("id"), row.get("alternatives")
        if oid not in expected or oid in found or not isinstance(groups, list) or len(groups) > 8:
            raise InvalidExtraction("invalid bound obligation")
        found.add(oid)
        alternatives = []
        for group in groups:
            if not isinstance(group, list) or not group or len(group) > 16:
                raise InvalidExtraction("invalid AND evidence set")
            refs = set()
            for key in group:
                if key in snap.nodes:
                    refs.update(_closure(key, plan, snap, config.graph_depth) if config.relations_enabled
                                else snap.nodes[key].source_ids)
                elif key in snap.sources:
                    refs.add(key)
                else:
                    raise InvalidExtraction("proof binding invented an evidence ID")
            if refs <= snap.sources.keys():
                alternatives.append(sorted(refs))
        expected[oid].alternatives = alternatives
    if found != expected.keys():
        raise InvalidExtraction("proof binding omitted an obligation")
    return plan


def _coverage(selected: frozenset[str], plan: QueryPlan) -> float:
    result = 0.0
    for o in plan.obligations:
        if not o.alternatives:
            continue
        best = max(len(selected.intersection(group)) / len(group) for group in o.alternatives if group)
        result += o.weight * (10 if best == 1 else 2 * best)
    return result


def select(plan: QueryPlan, evidence: dict[str, Evidence], config, top_k: int, budget: int,
           selector: str = "beam") -> list[Evidence]:
    limit = min(top_k, config.return_limit)
    if not evidence or limit <= 0 or budget <= 0 or config.char_budget <= 0:
        return []
    cost = {sid: tokens.evidence_cost(config, e) for sid, e in evidence.items()}
    chars = {sid: len(e.content) for sid, e in evidence.items()}
    max_score = max((e.score for e in evidence.values()), default=1) or 1

    def feasible(ids):
        return (len(ids) <= limit and sum(cost[sid] for sid in ids) <= budget
                and sum(chars[sid] for sid in ids) <= config.char_budget)

    def objective(ids):
        relevance = sum(evidence[sid].score / max_score for sid in ids)
        return _coverage(ids, plan) + relevance * 0.15 - sum(cost[sid] for sid in ids) / max(budget, 1) * 0.1

    bundles = {frozenset(group) for o in plan.obligations for group in o.alternatives
               if group and set(group) <= evidence.keys()}
    ranked = sorted(evidence, key=lambda sid: (-evidence[sid].score, sid))
    if selector == "topk":
        chosen = frozenset()
        for sid in ranked:
            proposed = chosen | {sid}
            if feasible(proposed):
                chosen = proposed
    elif selector == "greedy":
        chosen = frozenset()
        for _ in range(limit):
            options = [chosen | b for b in bundles if not b <= chosen and feasible(chosen | b)]
            if not options:
                break
            proposed = max(options, key=lambda ids: ((objective(ids) - objective(chosen)) /
                                                    max(1, sum(cost[s] for s in ids - chosen)), sorted(ids)))
            if objective(proposed) <= objective(chosen):
                break
            chosen = proposed
    else:
        beam = [frozenset()]
        # A state contains the selected source set and implicitly its satisfied AND/OR hypotheses.
        # Expand proof bundles together, rather than deciding a current fact before selecting evidence.
        for _ in range(min(limit, len(plan.obligations) + 2)):
            options = set(beam)
            for state in beam:
                options.update(state | bundle for bundle in bundles if not bundle <= state and feasible(state | bundle))
            next_beam = sorted(options, key=lambda ids: (-objective(ids), len(ids), sorted(ids)))[:config.beam_width]
            if next_beam == beam:
                break
            beam = next_beam
        chosen = beam[0]
    # Do not fill a proof-complete set with unrelated records simply to reach top_k.
    return sorted((evidence[sid] for sid in chosen), key=lambda e: (-e.score, e.id))


def retrieve(view, request, plan: QueryPlan, providers, config):
    queries = list(dict.fromkeys([request.query] + [o.query for o in plan.obligations]))
    vectors = providers.embed(queries)
    channels, per_query, channel_names = [], [], []
    for index, (query, vector) in enumerate(zip(queries, vectors)):
        dense = view.dense(vector, config.candidate_limit)
        lexical = view.lexical(query, config.candidate_limit)
        channels.extend([dense, lexical])
        channel_names.extend([f"dense:q{index}", f"lexical:q{index}"])
        per_query.append(set(dense) | set(lexical))
    temporal = view.temporal(plan.as_of.latest, config.candidate_limit)
    if temporal:
        channels.append(temporal)
        channel_names.append("temporal")
    fused, channel_ranks = merge(channels, cap=config.candidate_limit, names=channel_names)
    ranked = sorted(fused, key=lambda oid: (-fused[oid], oid))[:config.candidate_limit]
    plan.actions = ["PLAN", "SEARCH"]
    snap = view.snapshot(set(ranked), expand=config.graph_depth if config.relations_enabled else 0)
    if config.relations_enabled:
        plan.actions.extend(["TRAVERSE", "PROJECT_TIME", "FIND_COUNTEREVIDENCE", "DRILL_DOWN"])
        # Relevant state peers and counterevidence need not share the query's surface words.
        related = view.related(list(snap.nodes.values()))
        snap.sources.update(related.sources)
        snap.nodes.update(related.nodes)
        snap.spans.update(related.spans)
        snap.edges = list({e.id: e for e in snap.edges + related.edges}.values())
    source_scores = {sid: fused.get(sid, 0.0) for sid in snap.sources}
    for node in snap.nodes.values():
        score = fused.get(node.id, 0.0)
        # Temporal state preference only adjusts query-relevant objects, never arbitrary recent facts.
        lexical = overlap(request.query, node.representation())
        if lexical:
            score += max(0, _time_weight(node, plan, snap)) * 0.03
        for sid in node.source_ids:
            source_scores[sid] = max(source_scores.get(sid, 0), score)
    # A completely unrelated namespace should not turn dense nearest-neighbours into positive evidence.
    relevant = any(overlap(request.query, s.content) > 0 for s in snap.sources.values())
    if not relevant and hasattr(providers, "verify_candidates") and (
            config.profile != "full" or not hasattr(providers, "bind_obligations")):
        valid_ids = providers.verify_candidates(request.query, snap.sources)
        snap.sources = {sid: s for sid, s in snap.sources.items() if sid in valid_ids}
        snap.nodes = {nid: n for nid, n in snap.nodes.items() if set(n.source_ids) <= snap.sources.keys()}
    if config.rerank_url and snap.sources:
        reranked = providers.rerank(request.query, snap.sources)
        for sid, score in reranked.items():
            source_scores[sid] = score
    plan = proofs(plan, snap, fused, config)
    if config.profile == "full" and hasattr(providers, "bind_obligations"):
        plan = bind_proofs(plan, snap, request.query, providers, config)
    # One targeted gap iteration is bounded by the declared action/candidate budget.
    missing = [o for o in plan.obligations if not o.alternatives]
    if missing and config.gap_iterations:
        plan.actions.append("SEARCH")
        ids = set()
        for o in missing:
            ids.update(view.lexical(o.query, config.candidate_limit))
        extra = view.snapshot(ids, expand=config.graph_depth if config.relations_enabled else 0)
        snap.sources.update(extra.sources)
        snap.nodes.update(extra.nodes)
        snap.edges = list({e.id: e for e in snap.edges + extra.edges}.values())
        if config.profile == "full" and hasattr(providers, "bind_obligations"):
            plan = bind_proofs(plan, snap, request.query, providers, config)
        else:
            plan = proofs(plan, snap, fused, config)
    evidence = {sid: Evidence(sid, source.render(), source.asserted_at,
                             source_scores.get(sid, 0.0), sid) for sid, source in snap.sources.items()}
    token_budget = tokens.budget(config, request.query, request.options)
    # Exact spans rescue long sources that cannot fit as a whole. They have stable source-bound IDs.
    replacements = {}
    for sid, e in list(evidence.items()):
        if tokens.evidence_cost(config, e) <= token_budget and len(e.content) <= config.char_budget:
            continue
        matching = [s for s in snap.spans.values() if s.source_id == sid]
        matching.sort(key=lambda s: (-overlap(request.query, s.quote), s.id))
        if matching:
            span = matching[0]
            source = snap.sources[sid]
            stamp = f"[{source.asserted_at}]" if source.asserted_at else ""
            replacements[sid] = span.id
            evidence.pop(sid)
            evidence[span.id] = Evidence(span.id, f"{stamp}[{source.role}] {span.quote}", source.asserted_at, e.score, sid)
    if replacements:
        for o in plan.obligations:
            o.alternatives = [[replacements.get(s, s) for s in group] for group in o.alternatives]
    selected = select(plan, evidence, config, request.top_k, token_budget, config.selector)
    plan.actions.extend(["SELECT", "FINISH"])
    selected_ids = frozenset(e.id for e in selected)
    trace = {"generation": view.generation, "policy_generation": view.policy_generation,
             "plan": asdict(plan), "candidates": len(evidence), "selected_ids": sorted(selected_ids),
             "covered_obligations": [o.id for o in plan.obligations if any(set(g) <= selected_ids for g in o.alternatives)],
             "missing_obligations": [o.id for o in plan.obligations if not any(set(g) <= selected_ids for g in o.alternatives)],
             "source_paths": [asdict(e) for e in snap.edges],
             "channel_ranks": channel_ranks,
             "token_cost": sum(tokens.evidence_cost(config, e) for e in selected), "token_budget": token_budget}
    return selected, trace
