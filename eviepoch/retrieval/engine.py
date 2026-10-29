"""Hybrid discovery, query-conditioned proof bundles and bounded beam selection."""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, replace

import numpy as np

from . import tokens
from ..domain import InvalidExtraction, Obligation, QueryPlan, Snapshot
from .lexical import terms
from .context import temporal_value_request, worked_example_for_reference
from .fusion import merge
from ..providers import ProviderUnavailable
from ..manage.temporal import project_state_edges, project_query_state

RETRIEVAL_VERSION = "versioned-coverage-proof-v2"


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
            if node.kind in {"summary", "episode"}:
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
    for oid, alternatives in staged.items():
        expected[oid].alternatives = alternatives
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
    if (not isinstance(ids, list) or status not in {"relevant", "insufficient"}
            or any(not isinstance(sid, str) or sid not in audit_units for sid in ids)
            or bool(ids) != (status == "relevant")):
        raise ProviderUnavailable("evidence audit returned invalid or uncertain support")
    if not ids:
        return [], "no_support_in_candidates"
    allowed = {eid: e for eid, e in evidence.items() if eid in ids}
    # This is explicitly partial evidence. It does not turn an unfinished AND proof into a full proof.
    selected = select(plan, allowed, config, request.top_k, budget, "topk")
    return selected, "verified_fallback" if selected else "no_evidence_fits_budget"


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
    started = time.perf_counter()
    timings = {}
    queries = list(dict.fromkeys([request.query] + [o.query for o in plan.obligations]))
    vectors = providers.embed(queries)
    timings["embedding"] = (time.perf_counter() - started) * 1000
    stage = time.perf_counter()
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
    coverage_ids = []
    coverage_truncated = False
    if config.coverage_enabled and set(plan.intents) & {"global", "list", "order"}:
        coverage_limit = min(config.coverage_source_limit, config.candidate_limit)
        coverage_ids = view.coverage(" ".join(queries), coverage_limit + 1)
        coverage_truncated = len(coverage_ids) > coverage_limit
        coverage_ids = coverage_ids[:coverage_limit]
        reserved = coverage_ids[:config.candidate_limit]
        ranked = list(dict.fromkeys(reserved + ranked))[:config.candidate_limit]
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
        frontier = list(snap.nodes.values())
        for _ in range(config.graph_depth):
            linked = view.follow(frontier)
            frontier = [n for nid, n in linked.nodes.items() if nid not in snap.nodes]
            snap.sources.update(linked.sources)
            snap.nodes.update(linked.nodes)
            snap.spans.update(linked.spans)
            snap.edges = list({e.id: e for e in snap.edges + linked.edges}.values())
            if not frontier:
                break
    source_scores = {sid: fused.get(sid, 0.0) for sid in snap.sources}
    snap = project_state_edges(snap)
    if config.state_projection_enabled:
        snap = project_query_state(snap, plan)
    transfer_sources = [sid for sid, source in snap.sources.items() if worked_example_for_reference(request.query, source)]
    if transfer_sources and "procedure" not in plan.intents:
        plan.intents.append("procedure")
    for node in snap.nodes.values():
        score = fused.get(node.id, 0.0)
        # Temporal state preference only adjusts query-relevant objects, never arbitrary recent facts.
        lexical = overlap(request.query, node.representation())
        if lexical:
            score += _time_weight(node, plan, snap) * 0.03
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
    timings["discovery"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    binding_status = "disabled"
    binding_enabled = (config.profile == "full" and config.proof_binding_enabled
                       and hasattr(providers, "bind_obligations"))
    if binding_enabled:
        try:
            plan = bind_proofs(plan, snap, request.query, providers, config)
            binding_status = "bound" if any(o.alternatives for o in plan.obligations) else "empty"
        except (InvalidExtraction, ProviderUnavailable) as exc:
            binding_status = "invalid" if isinstance(exc, InvalidExtraction) else "unavailable"
            for obligation in plan.obligations:
                obligation.alternatives = []
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
        snap.spans.update(extra.spans)
        snap.edges = list({e.id: e for e in snap.edges + extra.edges}.values())
        snap = project_state_edges(snap)
        if config.state_projection_enabled:
            snap = project_query_state(snap, plan)
        if binding_enabled and binding_status not in {"invalid", "unavailable"}:
            try:
                plan = bind_proofs(plan, snap, request.query, providers, config)
                binding_status = "bound" if any(o.alternatives for o in plan.obligations) else "empty"
            except (InvalidExtraction, ProviderUnavailable) as exc:
                binding_status = "invalid" if isinstance(exc, InvalidExtraction) else "unavailable"
                for obligation in plan.obligations:
                    obligation.alternatives = []
        elif not binding_enabled:
            plan = proofs(plan, snap, fused, config)
    timings["binding"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    if coverage_ids:
        coverage_sources = {sid: snap.sources[sid] for sid in coverage_ids if sid in snap.sources}
        verify = getattr(providers, "verify_candidates", None)
        allowed = verify(request.query, coverage_sources) if verify and coverage_sources else set()
        if not isinstance(allowed, (set, list)) or not set(allowed) <= coverage_sources.keys():
            raise ProviderUnavailable("coverage verification returned invalid IDs", stage="coverage")
        for i, sid in enumerate(coverage_ids):
            if sid in allowed:
                plan.obligations.append(Obligation(f"coverage_{i}", request.query, alternatives=[[sid]]))
        if coverage_truncated:
            plan.obligations.append(Obligation("coverage_remainder", request.query))
    if transfer_sources:
        live_examples = [[sid] for sid in transfer_sources if sid in snap.sources]
        if live_examples:
            # An opaque task-reference adapter provides no target-instance facts.
            # The stored, same-domain worked example is an available procedure,
            # not a claim that its task ID equals the new one or solves it.
            plan.obligations.append(Obligation("worked_example", "source-backed tool experience", alternatives=live_examples))
    evidence = {sid: Evidence(sid, source.render(), source.evidence_time,
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
            stamp = source.render()[:-len(source.content)]
            replacements[sid] = span.id
            evidence.pop(sid)
            evidence[span.id] = Evidence(span.id, f"{stamp}{span.quote}", source.evidence_time, e.score, sid)
    # A source-level AND proof is not automatically satisfied by one replacement span.
    # Unaffordable full-source groups remain missing; audited spans are partial fallback evidence.
    recorder = getattr(providers, "record_selection", None)
    if recorder is not None:
        recorder(request.query, plan, evidence, token_budget, config)
    selected = select(plan, evidence, config, request.top_k, token_budget, config.selector)
    selection_status = "proof_selected" if selected else "no_evidence_fits_budget"
    if not selected and binding_enabled and token_budget > 0 and request.top_k > 0 and config.char_budget > 0:
        selected, selection_status = controlled_fallback(request, plan, snap, evidence, providers, config, token_budget)
    timings["selection"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    if config.disclosure_enabled:
        from .disclosure import disclose
        selected, reviewed = disclose(request.query, plan, selected, snap, providers)
        if reviewed:
            # Disclosure can split/drop a source. Reapply all final-output budgets and
            # never claim that an arbitrary excerpt completed a whole-source proof.
            selected = select(plan, {e.id: e for e in selected}, config, request.top_k, token_budget, "topk")
            selection_status = "disclosed_spans" if selected else "no_disclosable_support"
    audit = getattr(providers, "audit_attribute_support", None) or getattr(providers, "audit_evidence", None)
    if (config.support_audit_enabled and audit is not None and selected and not transfer_sources
            and not temporal_value_request(request.query, plan)
            and not set(plan.intents) & {"global", "list", "order", "why", "procedure", "history", "comparison"}):
        units = {e.id: replace(snap.sources[e.source_id], id=e.id, content=e.content) for e in selected}
        support = audit(request.query, plan, units)
        if (not isinstance(support, dict) or support.get("status") not in {"relevant", "insufficient", "uncertain"}
                or not isinstance(support.get("ids"), list)
                or any(not isinstance(eid, str) or eid not in units for eid in support["ids"])
                or bool(support["ids"]) != (support["status"] in {"relevant", "uncertain"})):
            raise ProviderUnavailable("invalid final attribute support audit", stage="support_audit")
        selected = [e for e in selected if e.id in support["ids"]]
        if not selected:
            selection_status = "no_attribute_support"
    if "order" in plan.intents:
        from ..ingest.temporal import anchor_for
        def order_key(e):
            source = snap.sources[e.source_id]
            anchor = anchor_for(source).get("time") or "9999"
            if plan.order_by == "record":
                return ("", source.generation, source.ordinal, e.id)
            if plan.order_by == "event":
                events = [n for n in snap.nodes.values() if e.source_id in n.source_ids and n.occurrence_time.earliest]
                events.sort(key=lambda n: (-overlap(request.query, n.representation()), n.occurrence_time.earliest, n.id))
                if events:
                    anchor = events[0].occurrence_time.earliest
            return (anchor, source.generation, source.ordinal, e.id)
        selected.sort(key=order_key)
    timings["disclosure"] = (time.perf_counter() - stage) * 1000
    plan.actions.extend(["SELECT", "FINISH"])
    selected_ids = frozenset(e.id for e in selected)
    trace = {"generation": view.generation, "policy_generation": view.policy_generation,
             "plan": asdict(plan), "candidates": len(evidence), "selected_ids": sorted(selected_ids),
             "returned_source_ids": sorted({e.source_id for e in selected}),
             "evidence_sources": {e.id: e.source_id for e in selected},
             "binding_status": binding_status, "selection_status": selection_status,
             "timings_ms": timings, "coverage_candidates": len(coverage_ids),
             "coverage_truncated": coverage_truncated,
             "worked_example_candidates": len(transfer_sources),
             "covered_obligations": [o.id for o in plan.obligations if any(set(g) <= selected_ids for g in o.alternatives)],
             "missing_obligations": [o.id for o in plan.obligations if not any(set(g) <= selected_ids for g in o.alternatives)],
             "source_paths": [asdict(e) for e in snap.edges],
             "channel_ranks": channel_ranks,
             "token_cost": sum(tokens.evidence_cost(config, e) for e in selected), "token_budget": token_budget}
    return selected, trace
