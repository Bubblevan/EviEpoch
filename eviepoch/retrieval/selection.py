"""Budget-aware evidence selection and partial proof retention."""
from __future__ import annotations

from ..domain import QueryPlan
from . import tokens
from .evidence import Evidence, _packed_source_items

def _coverage(selected: frozenset[str], plan: QueryPlan,
              evidence: dict[str, Evidence] | None = None) -> float:
    source_coverage = {}
    for eid in selected:
        item = evidence[eid] if evidence is not None else None
        source_id = item.source_id if item is not None else eid
        # A source-exact fragment is useful partial support, but cannot by itself
        # satisfy a source-level proof obligation.
        amount = 1.0 if item is None or eid == source_id else 0.5
        source_coverage[source_id] = max(source_coverage.get(source_id, 0.0), amount)
    result = 0.0
    for o in plan.obligations:
        if not o.alternatives:
            continue
        groups = [group for group in o.alternatives if group]
        if not groups:
            continue
        best = max(sum(source_coverage.get(sid, 0.0) for sid in group) / len(group)
                   for group in groups)
        result += o.weight * (10 if best == 1 else 2 * best)
    return result


def select(plan: QueryPlan, evidence: dict[str, Evidence], config, top_k: int, budget: int,
           selector: str = "beam", pack_output: bool = True) -> list[Evidence]:
    limit = min(top_k, config.return_limit)
    if not evidence or limit <= 0 or budget <= 0 or config.char_budget <= 0:
        return []
    score_values = [float(e.score) for e in evidence.values()]
    min_score, max_score = min(score_values), max(score_values)
    score_range = max_score - min_score

    def relevance(sid):
        if score_range <= 1e-12:
            return 1.0
        return (evidence[sid].score - min_score) / score_range

    def feasible(ids):
        packed = _packed_source_items(ids, evidence)
        return (len(packed) <= limit
                and sum(tokens.evidence_cost(config, item) for item in packed) <= budget
                and sum(len(item.content) for item in packed) <= config.char_budget)

    def objective(ids):
        relevance_total = sum(relevance(sid) for sid in ids)
        packed_cost = sum(tokens.evidence_cost(config, item)
                          for item in _packed_source_items(ids, evidence))
        return (_coverage(ids, plan, evidence) + relevance_total * 0.15
                - packed_cost / max(budget, 1) * 0.1)

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
        cost = {sid: tokens.evidence_cost(config, item) for sid, item in evidence.items()}
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
    def proof_complete(ids):
        if not plan.obligations:
            return True
        coverage = {}
        for eid in ids:
            item = evidence[eid]
            coverage[item.source_id] = max(coverage.get(item.source_id, 0.0),
                                           1.0 if eid == item.source_id else 0.5)
        return all(any(group and all(coverage.get(source_id, 0.0) == 1.0 for source_id in group)
                       for group in obligation.alternatives)
                   for obligation in plan.obligations)

    if not proof_complete(chosen):
        # A missing or unaffordable complete AND bundle must not erase the best
        # available support. Select high-scoring individual evidence units and
        # give partial obligation coverage enough weight to retain useful premises.
        partial_ranked = [sid for sid in ranked if relevance(sid) >= 0.25]

        def partial_objective(ids):
            relevance_total = sum(relevance(sid) for sid in ids)
            packed_cost = sum(tokens.evidence_cost(config, item)
                              for item in _packed_source_items(ids, evidence))
            return (_coverage(ids, plan, evidence) + relevance_total * 0.6
                    - packed_cost / max(budget, 1) * 0.1)

        partial = chosen
        remaining = set(partial_ranked) - set(partial)
        while remaining:
            options = []
            for sid in remaining:
                proposed = partial | {sid}
                if feasible(proposed):
                    gain = partial_objective(proposed) - partial_objective(partial)
                    options.append((gain, evidence[sid].score, sid, proposed))
            if not options:
                break
            gain, _, sid, proposed = max(options, key=lambda row: (row[0], row[1], row[2]))
            if gain <= 0:
                break
            partial = proposed
            remaining.remove(sid)
        chosen = partial
    # Do not fill a proof-complete set with unrelated records simply to reach top_k.
    selected = (_packed_source_items(chosen, evidence) if pack_output
                else [evidence[sid] for sid in chosen])
    return sorted(selected, key=lambda e: (-e.score, e.source_id, e.source_start, e.id))
