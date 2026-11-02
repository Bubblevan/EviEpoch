"""Hybrid retrieval orchestration: discover, project, prove, disclose, and select."""
from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, replace

from ..domain import InvalidExtraction, Obligation, QueryPlan
from ..manage.temporal import project_state_edges, project_query_state
from ..providers import ProviderUnavailable
from . import tokens
from .context import (has_disclosure_restriction, temporal_value_request,
                      worked_example_for_reference)
from .evidence import Evidence, _annotate_source_span, _packed_source_items, _paragraph_at
from .fusion import merge
from .graph import (_relation_candidate_channel, _source_ids_for_objects,
                    retrieve as retrieve_graph)
from .proofs import (_closure, _fallback_sources, _proof_candidate_sources, _time_weight,
                     bind_proofs, controlled_fallback, proofs)
from .reranking import _bounded_rerank_ids, _proof_score_map
from .scoring import overlap
from .selection import _coverage, select

RETRIEVAL_VERSION = "source-unit-context-proof-v4-multispan"

_TEMPORAL_RELATION_QUERY = re.compile(
    r"\b(?:after|before|later|earlier|preced(?:e|ed|ing)|following|how long|elapsed|duration|"
    r"how many (?:days?|weeks?|months?|years?)|between|since|timeline|chronolog(?:y|ical))\b|"
    r"之后|之前|以后|以前|多久|几天|几周|几个月|几年|间隔|先后|时间顺序|时间线|何时|什么时候|"
    r"最早|最晚|第一次|最后一次",
    re.IGNORECASE,
)

def fuse(channels: list[dict[str, float]], k: int = 60) -> dict[str, float]:
    return merge(channels, k)[0]

def retrieve(view, request, plan: QueryPlan, providers, config, task_adapter=None):
    started = time.perf_counter()
    timings = {}
    queries = list(dict.fromkeys([request.query_text] + [o.query for o in plan.obligations]))
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
    temporal_modes = []
    if plan.as_of.latest:
        temporal_modes.append(("valid", plan.as_of.earliest or plan.as_of.latest, plan.as_of.latest))
        temporal_modes.append(("mention", plan.as_of.earliest or plan.as_of.latest, plan.as_of.latest))
    if (set(plan.intents) & {"comparison", "history", "order"}
            or _TEMPORAL_RELATION_QUERY.search(request.query_text)):
        temporal_modes.append(("occurrence", None, None))
    if plan.order_by == "first_mention":
        temporal_modes.append(("asserted", plan.as_of.earliest, plan.as_of.latest))
    elif plan.order_by == "record":
        temporal_modes.append(("stored", plan.as_of.earliest, plan.as_of.latest))
    temporal_candidates = {}
    for mode, date_start, date_end in temporal_modes:
        retrieved_temporal = view.temporal(date_start, config.candidate_limit, date_end=date_end,
                                           mode=mode, query=request.query_text)
        if retrieved_temporal:
            channels.append(retrieved_temporal)
            temporal_candidates.update(retrieved_temporal)
            channel_names.append(f"temporal_{mode}")
    graph_candidates = retrieve_graph(view, request.query_text, plan, config)
    if graph_candidates:
        channels.append(graph_candidates)
        channel_names.append("graph")
    if task_adapter is not None:
        extension = task_adapter.extend_retrieval(view, request, plan)
        for channel, name in zip(extension.extra_channels, extension.channel_names):
            if channel:
                channels.append(channel)
                channel_names.append(name)
        if extension.trace:
            coding_trace = extension.trace
        else:
            coding_trace = {}
    else:
        coding_trace = {}
    fused, channel_ranks = merge(channels, cap=config.candidate_limit, names=channel_names)
    if task_adapter is not None:
        with view.repo.view(view.ns) as db_view:
            if getattr(task_adapter, "__class__", type(task_adapter)).__name__ == "MultimodalAdapter":
                from ..adapters.multimodal.search import promote_multimodal_hits
                fused = promote_multimodal_hits(db_view, fused)
            else:
                from ..adapters.coding.search import promote_coding_hits
                fused = promote_coding_hits(db_view, fused)
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
    seed_ids = set(ranked)
    plan.actions = ["PLAN", "SEARCH"]
    snap = view.snapshot(set(ranked), expand=config.graph_depth if config.relations_enabled else 0)
    context_ids = []
    if config.context_expansion_enabled and set(plan.intents) & {"order", "history", "global"}:
        seeds = [snap.sources[sid] for sid in coverage_ids if sid in snap.sources]
        if not seeds:
            seeds = sorted(snap.sources.values(), key=lambda s: (-fused.get(s.id, 0), s.id))[:8]
        neighbor = getattr(view, "source_neighbors", None)
        if neighbor is not None:
            context = neighbor(seeds, min(4, config.window_radius), min(32, config.candidate_limit))
            context_ids = list(context.sources)
            snap.sources.update(context.sources)
            snap.nodes.update(context.nodes)
            snap.spans.update(context.spans)
            snap.edges = list({e.id: e for e in snap.edges + context.edges}.values())
            # Neighbors are candidates, not approved evidence. Verification and all
            # time/governance/disclosure/budget checks still apply below.
            if config.coverage_enabled:
                coverage_ids = list(dict.fromkeys(coverage_ids + context_ids))
                coverage_ids = coverage_ids[:min(config.coverage_source_limit, config.candidate_limit)]
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
    relation_channel = (_relation_candidate_channel(snap, seed_ids, request.query_text, config.graph_depth)
                        if config.relations_enabled else {})
    if relation_channel:
        # Dense, lexical, temporal, and graph-expanded relation candidates now
        # share one RRF ordering before the bounded cross-encoder pool is built.
        channels.append(relation_channel)
        channel_names.append("relation")
        fused, channel_ranks = merge(channels, cap=config.candidate_limit, names=channel_names)
    source_scores = {sid: fused.get(sid, 0.0) for sid in snap.sources}
    for span_id, span in snap.spans.items():
        if span_id in fused and span.source_id in source_scores:
            source_scores[span.source_id] = max(source_scores[span.source_id], fused[span_id])
    transfer_sources = [sid for sid, source in snap.sources.items() if worked_example_for_reference(request.query_text, source)]
    if transfer_sources and "procedure" not in plan.intents:
        plan.intents.append("procedure")
    for node in snap.nodes.values():
        score = fused.get(node.id, 0.0)
        # Temporal state preference only adjusts query-relevant objects, never arbitrary recent facts.
        lexical = overlap(request.query_text, node.representation())
        if lexical:
            score += _time_weight(node, plan, snap) * 0.03
        for sid in node.source_ids:
            source_scores[sid] = max(source_scores.get(sid, 0), score)
    # A completely unrelated namespace should not turn dense nearest-neighbours into positive evidence.
    relevant = any(overlap(request.query_text, s.content) > 0 for s in snap.sources.values())
    if not relevant and hasattr(providers, "verify_candidates") and (
            config.profile != "full" or not hasattr(providers, "bind_obligations")):
        valid_ids = providers.verify_candidates(request.query_text, snap.sources)
        snap.sources = {sid: s for sid, s in snap.sources.items() if sid in valid_ids}
        snap.nodes = {nid: n for nid, n in snap.nodes.items() if set(n.source_ids) <= snap.sources.keys()}

    reranked_source_ids: set[str] = set()
    rerank_calls = 0
    rerank_attempts = 0
    rerank_duration_ms = 0.0
    rerank_status = "disabled" if not config.rerank_enabled else "skipped_empty"
    reranker = getattr(providers, "rerank", None)

    def rerank_sources(scope=None):
        nonlocal rerank_attempts, rerank_calls, rerank_duration_ms, rerank_status
        if not config.rerank_enabled:
            return False
        pool_ids = _bounded_rerank_ids(snap, source_scores, fused, temporal_candidates.keys(),
                                       request.query_text, config, scope)
        if not pool_ids:
            return False
        if reranker is None or not config.rerank_url:
            rerank_status = "unavailable"
            return False
        pool = {sid: snap.sources[sid] for sid in pool_ids}
        rerank_started = time.perf_counter()
        try:
            rerank_attempts += 1
            scores = reranker(request.query_text, pool)
            valid_scores = isinstance(scores, dict) and set(scores) == set(pool)
            if valid_scores:
                try:
                    valid_scores = all(not isinstance(score, bool) and math.isfinite(float(score))
                                       for score in scores.values())
                except (TypeError, ValueError, OverflowError):
                    valid_scores = False
            if not valid_scores:
                raise ProviderUnavailable("reranker returned an incomplete or invalid score set", stage="rerank")
            source_scores.update({sid: float(scores[sid]) for sid in pool})
            reranked_source_ids.update(pool)
            rerank_calls += 1
            rerank_status = "applied" if rerank_calls == 1 else "applied_gap"
            return True
        except ProviderUnavailable:
            rerank_status = "degraded" if not reranked_source_ids else "applied_with_degraded_gap"
            return False
        finally:
            rerank_duration_ms += (time.perf_counter() - rerank_started) * 1000

    # The cross-encoder is a bounded intermediate stage. Unscored sources stay
    # in the snapshot and can be brought back by local proofs or the gap search.
    rerank_sources()
    # State projection and proof construction consume the reranked candidate scores.
    snap = project_state_edges(snap)
    if config.state_projection_enabled:
        snap = project_query_state(snap, plan)

    def refresh_proofs():
        if reranked_source_ids:
            scores = _proof_score_map(snap, source_scores, reranked_source_ids)
            return proofs(plan, snap, scores, config, reranked_source_ids)
        return proofs(plan, snap, fused, config)

    plan = refresh_proofs()
    local_proof_sources = {sid for obligation in plan.obligations
                           for group in obligation.alternatives for sid in group}
    local_proof_sources.update(_proof_candidate_sources(plan, snap, source_scores, config))
    timings["discovery"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    binding_status = "disabled"
    binding_enabled = (config.profile == "full" and config.proof_binding_enabled
                       and hasattr(providers, "bind_obligations"))
    if binding_enabled:
        try:
            plan = bind_proofs(plan, snap, request.query_text, providers, config)
            binding_status = "bound" if any(o.alternatives for o in plan.obligations) else "empty"
        except (InvalidExtraction, ProviderUnavailable) as exc:
            binding_status = "invalid" if isinstance(exc, InvalidExtraction) else "unavailable"
            for obligation in plan.obligations:
                obligation.alternatives = []
    # One targeted gap iteration is bounded by the declared action/candidate budget.
    missing = [o for o in plan.obligations if not o.alternatives]
    gap_source_ids = set()
    if missing and config.gap_iterations:
        plan.actions.append("SEARCH")
        ids = set()
        for o in missing:
            ids.update(view.lexical(o.query, config.candidate_limit))
        extra = view.snapshot(ids, expand=config.graph_depth if config.relations_enabled else 0)
        gap_source_ids.update(extra.sources)
        snap.sources.update(extra.sources)
        snap.nodes.update(extra.nodes)
        snap.spans.update(extra.spans)
        snap.edges = list({e.id: e for e in snap.edges + extra.edges}.values())
        for sid, source in extra.sources.items():
            source_scores.setdefault(sid, fused.get(sid, overlap(request.query_text, source.content) * 0.01))
    current_proof_sources = {sid for obligation in plan.obligations
                             for group in obligation.alternatives for sid in group}
    supplemental_scope = (local_proof_sources | current_proof_sources | gap_source_ids
                          | set(coverage_ids) | set(transfer_sources)) - reranked_source_ids
    if supplemental_scope and reranked_source_ids:
        rerank_sources(supplemental_scope)
    if gap_source_ids:
        snap = project_state_edges(snap)
        if config.state_projection_enabled:
            snap = project_query_state(snap, plan)
    plan = refresh_proofs()
    if gap_source_ids and binding_enabled and binding_status not in {"invalid", "unavailable"}:
        try:
            plan = bind_proofs(plan, snap, request.query_text, providers, config)
            binding_status = "bound" if any(o.alternatives for o in plan.obligations) else "empty"
        except (InvalidExtraction, ProviderUnavailable) as exc:
            binding_status = "invalid" if isinstance(exc, InvalidExtraction) else "unavailable"
            for obligation in plan.obligations:
                obligation.alternatives = []
    if (not plan.obligations or any(not o.alternatives for o in plan.obligations)
            or binding_status in {"invalid", "unavailable", "empty"}):
        plan = refresh_proofs()
    timings["binding"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    if coverage_ids:
        coverage_sources = {sid: snap.sources[sid] for sid in coverage_ids if sid in snap.sources}
        verify = getattr(providers, "verify_candidates", None)
        allowed = verify(request.query_text, coverage_sources) if verify and coverage_sources else set()
        if not isinstance(allowed, (set, list)) or not set(allowed) <= coverage_sources.keys():
            raise ProviderUnavailable("coverage verification returned invalid IDs", stage="coverage")
        for i, sid in enumerate(coverage_ids):
            if sid in allowed:
                plan.obligations.append(Obligation(f"coverage_{i}", request.query_text, alternatives=[[sid]]))
        if coverage_truncated:
            plan.obligations.append(Obligation("coverage_remainder", request.query_text))
    if transfer_sources:
        live_examples = [[sid] for sid in transfer_sources if sid in snap.sources]
        if live_examples:
            # An opaque task-reference adapter provides no target-instance facts.
            # The stored, same-domain worked example is an available procedure,
            # not a claim that its task ID equals the new one or solves it.
            plan.obligations.append(Obligation("worked_example", "source-backed tool experience", alternatives=live_examples))
    evidence = {sid: Evidence(sid, source.render(), source.evidence_time,
                             source_scores.get(sid, 0.0), sid) for sid, source in snap.sources.items()}
    token_budget = tokens.budget(config, request.query_text, request.options)
    # Keep several exact, disjoint excerpts from oversized sources. Selection
    # treats same-source excerpts as one output slot and packs them after review.
    for sid, e in list(evidence.items()):
        if tokens.evidence_cost(config, e) <= token_budget and len(e.content) <= config.char_budget:
            continue
        matching = [s for s in snap.spans.values() if s.source_id == sid]
        queries = [request.query_text] + [o.query for o in plan.obligations]
        constraint = re.compile(
            r"\b(?:except(?:ion)?s?|unless|never|must|only|not|before|after|requires?|required|"
            r"prerequisite|provided that|subject to)\b|例外|除非|禁止|必须|不得|过敏|前提|条件|仅当",
            re.I)
        premise = re.compile(
            r"\b(?:must|only if|requires?|required|prerequisite|provided that|subject to|"
            r"before|if|when|condition)\b|必须|仅当|前提|条件|如果|若|需要",
            re.I)
        exception = re.compile(
            r"\b(?:exception|except|unless|excluding|otherwise)\b|例外|除非|除外",
            re.I)
        rule_source_ids = {sid for edge in snap.edges
                           if edge.kind in {"REQUIRES", "EXCEPT_IF", "RULE_VERSION"}
                           for sid in edge.source_ids}
        rule_source_ids.update(sid for node in snap.nodes.values()
                               if node.kind in {"rule", "procedure"} for sid in node.source_ids)
        preserve_rule_clauses = sid in rule_source_ids or "procedure" in plan.intents
        ranked_spans = []
        for span in matching:
            lexical = max((overlap(query, span.quote) for query in queries), default=0.0)
            has_constraint = bool(constraint.search(span.quote))
            relevance = lexical + (0.15 if has_constraint else 0.0)
            has_rule_clause = bool(premise.search(span.quote) or exception.search(span.quote))
            if relevance > 0 or (preserve_rule_clauses and has_rule_clause):
                ranked_spans.append((relevance, lexical, span))
        ranked_spans.sort(key=lambda row: (-row[0], -row[1], row[2].byte_start, row[2].id))
        if not ranked_spans:
            continue
        source = snap.sources[sid]
        stamp = (f"[{source.role}] " if has_disclosure_restriction(request.query_text)
                 else source.render()[:-len(source.content)] if source.content else source.render())
        picked, ranges = [], []

        def add_span(span):
            if any(span.byte_start < end and span.byte_end > start for start, end in ranges):
                return False
            paragraph = _paragraph_at(source.content, span.byte_start)
            item = Evidence(
                span.id, f"{stamp}{span.quote}", source.evidence_time, e.score, sid,
                source_start=span.byte_start, source_spans=(span.id,),
                source_ranges=((span.byte_start, span.byte_end),), source_prefix=stamp,
                source_paragraphs=(paragraph,))
            if (len(item.content) > config.char_budget
                    or tokens.evidence_cost(config, item) > token_budget):
                return False
            picked.append(item)
            ranges.append((span.byte_start, span.byte_end))
            return True

        if preserve_rule_clauses:
            # Reserve one high-relevance precondition and one exception when both
            # exist; one span that contains both naturally satisfies both roles.
            for pattern in (premise, exception):
                if any(pattern.search(item.content) for item in picked):
                    continue
                for _, _, span in ranked_spans:
                    if pattern.search(span.quote) and add_span(span):
                        break
        for _, _, span in ranked_spans:
            if len(picked) >= 3:
                break
            add_span(span)
        if picked:
            evidence.pop(sid)
            evidence.update((item.id, item) for item in picked)
    # A fragment never claims that its full source-level proof group was satisfied.
    recorder = getattr(providers, "record_selection", None)
    if recorder is not None:
        recorder(request.query_text, plan, evidence, token_budget, config)
    selected = select(plan, evidence, config, request.top_k, token_budget,
                      config.selector, pack_output=False)
    selected_ids = {e.id for e in selected}
    proof_complete = all(
        any(group and set(group) <= selected_ids for group in obligation.alternatives)
        for obligation in plan.obligations)
    selection_status = ("proof_selected" if selected and proof_complete
                        else "partial_selected" if selected else "no_evidence_fits_budget")
    if (binding_enabled and not proof_complete and token_budget > 0
            and request.top_k > 0 and config.char_budget > 0):
        selected, selection_status = controlled_fallback(request, plan, snap, evidence, providers, config, token_budget)
    timings["selection"] = (time.perf_counter() - stage) * 1000
    stage = time.perf_counter()
    reviewed = False
    if config.disclosure_enabled:
        from .disclosure import disclose, pack_excerpts
        selected, reviewed = disclose(request.query_text, plan, selected, snap, providers)
        if reviewed:
            selection_status = "disclosed_spans" if selected else "no_disclosable_support"
    else:
        from .disclosure import pack_excerpts
    for item in selected:
        _annotate_source_span(item, snap)
    same_source_spans = {}
    for item in selected:
        if item.source_ranges and item.id != item.source_id:
            same_source_spans.setdefault(item.source_id, []).append(item)
    needs_span_pack = any(len(items) > 1 for items in same_source_spans.values())
    if needs_span_pack:
        # Force same-source excerpts into one return item even when top_k has room.
        selected = pack_excerpts(request.query_text, selected, snap, max(0, len(selected) - 1))
    elif reviewed:
        selected = pack_excerpts(request.query_text, selected, snap, min(request.top_k, config.return_limit))
    if reviewed or needs_span_pack:
        # Disclosure can split/drop a source. Reapply all final-output budgets and
        # never claim that an arbitrary excerpt completed a whole-source proof.
        selected = select(plan, {e.id: e for e in selected}, config, request.top_k, token_budget, "topk")
    for item in selected:
        _annotate_source_span(item, snap)
        if item.source_ranges and not item.source_paragraphs:
            content = snap.sources[item.source_id].content
            item.source_paragraphs = tuple(_paragraph_at(content, start)
                                           for start, _ in item.source_ranges)
    audit = getattr(providers, "audit_attribute_support", None) or getattr(providers, "audit_evidence", None)
    if (config.support_audit_enabled and audit is not None and selected and not transfer_sources
            and not temporal_value_request(request.query_text, plan)
            and not set(plan.intents) & {"global", "list", "order", "why", "procedure", "history", "comparison"}):
        units = {e.id: replace(snap.sources[e.source_id], id=e.id, content=e.content) for e in selected}
        support = audit(request.query_text, plan, units)
        if (not isinstance(support, dict) or support.get("status") not in {"relevant", "insufficient", "uncertain"}
                or not isinstance(support.get("ids"), list)
                or any(not isinstance(eid, str) or eid not in units for eid in support["ids"])
                or bool(support["ids"]) != (support["status"] in {"relevant", "uncertain"})):
            raise ProviderUnavailable("invalid final attribute support audit", stage="support_audit")
        selected = [e for e in selected if e.id in support["ids"]]
        if not selected:
            selection_status = "no_attribute_support"
    # Preserve intra-source offsets even for non-temporal questions. Disjoint
    # fragments of one correction/rule must not be shuffled by hash tie-breaking.
    selected.sort(key=lambda e: (-e.score, e.source_id, e.source_start, e.id))
    if "order" in plan.intents:
        from ..ingest.temporal import anchor_for
        def order_key(e):
            source = snap.sources[e.source_id]
            anchor = anchor_for(source).get("time") or "9999"
            if plan.order_by == "record":
                return ("", source.generation, source.ordinal, e.source_start, e.id)
            if plan.order_by == "event":
                events = [n for n in snap.nodes.values() if e.source_id in n.source_ids and n.occurrence_time.earliest]
                events.sort(key=lambda n: (-overlap(request.query_text, n.representation()), n.occurrence_time.earliest, n.id))
                if events:
                    anchor = events[0].occurrence_time.earliest
            return (anchor, source.generation, source.ordinal, e.source_start, e.id)
        selected.sort(key=order_key)
    timings["disclosure"] = (time.perf_counter() - stage) * 1000
    timings["rerank"] = rerank_duration_ms
    plan.actions.extend(["SELECT", "FINISH"])
    selected_ids = frozenset(e.id for e in selected)
    trace = {"generation": view.generation, "policy_generation": view.policy_generation,
             "plan": asdict(plan), "candidates": len(evidence), "selected_ids": sorted(selected_ids),
             "returned_source_ids": sorted({e.source_id for e in selected}),
             "evidence_sources": {e.id: e.source_id for e in selected},
             "evidence_spans": {e.id: list(e.source_spans) for e in selected if e.source_spans},
             "evidence_ranges": {e.id: list(e.source_ranges) for e in selected if e.source_ranges},
             "evidence_paragraphs": {e.id: list(e.source_paragraphs) for e in selected if e.source_paragraphs},
             "binding_status": binding_status, "selection_status": selection_status,
             "rerank_status": rerank_status,
             "rerank_attempts": rerank_attempts, "rerank_calls": rerank_calls,
             "rerank_scored_candidates": len(reranked_source_ids),
             "rerank_candidate_limit": config.rerank_candidate_limit,
             "rerank_category_quota": config.rerank_category_quota,
             "rerank_max_segments": config.rerank_max_segments,
             "timings_ms": timings, "coverage_candidates": len(coverage_ids),
             "coverage_truncated": coverage_truncated,
             "worked_example_candidates": len(transfer_sources),
             "context_candidates": len(context_ids),
             "retrieval_channels": {name: len(channel) for name, channel in zip(channel_names, channels)},
             "covered_obligations": [o.id for o in plan.obligations if any(set(g) <= selected_ids for g in o.alternatives)],
             "missing_obligations": [o.id for o in plan.obligations if not any(set(g) <= selected_ids for g in o.alternatives)],
             "source_paths": [asdict(e) for e in snap.edges],
             "channel_ranks": channel_ranks,
             "token_cost": sum(tokens.evidence_cost(config, e) for e in selected), "token_budget": token_budget}
    if coding_trace:
        trace["coding"] = coding_trace
    return selected, trace
