"""Append-only state relations, extractive consolidation and counterexample revision."""
from __future__ import annotations

from collections import defaultdict

from ..domain import Edge, Node, Snapshot, stable_id


def reconcile(new: list[Node], edges: list[Edge], old: Snapshot, *, consolidate=True, reflect=True):
    by_key = defaultdict(list)
    for node in old.nodes.values():
        if node.kind not in {"summary", "reflection"}:
            by_key[node.key].append(node)
    output = {n.id: n for n in new}
    relations = {e.id: e for e in edges}

    def link(origin, target, kind):
        refs = sorted(set(origin.source_ids + target.source_ids))
        eid = stable_id(origin.id, target.id, kind)
        relations[eid] = Edge(eid, origin.id, target.id, kind, refs, "validated_attribute_comparison")

    for node in new:
        node.state = "hypothesis" if node.kind == "reflection" else "supported"
        node.confidence = 0.5
        for previous in by_key[node.key]:
            # Multi-valued properties and different modalities never imply a replacement.
            if node.modality != previous.modality or node.modality in {"planned", "hypothetical"}:
                continue
            if node.value.casefold() == previous.value.casefold():
                link(node, previous, "SUPPORTS")
            elif node.cardinality == previous.cardinality == "single":
                if node.change == "correction":
                    link(node, previous, "CORRECTS")
                elif node.change == "retraction":
                    link(node, previous, "RETRACTS")
                elif (node.change == "change" and node.valid_time.earliest and previous.valid_time.latest
                      and node.valid_time.earliest > previous.valid_time.latest):
                    link(node, previous, "SUPERSEDES")
                else:
                    link(node, previous, "CONTRADICTS")
        by_key[node.key].append(node)

    # Explicit counterexamples challenge general lessons; retrieval frequency never changes support.
    all_nodes = {**old.nodes, **output}
    if reflect:
        for reflection in [n for n in all_nodes.values() if n.kind == "reflection"]:
            support, counter = set(reflection.support_ids), set(reflection.counterexample_ids)
            for edge in relations.values():
                if edge.target != reflection.id:
                    continue
                if edge.kind == "SUPPORTS":
                    support.add(edge.origin)
                elif edge.kind in {"CONTRADICTS", "CORRECTS", "RETRACTS"}:
                    counter.add(edge.origin)
            reflection.support_ids = sorted(support)
            reflection.counterexample_ids = sorted(counter)
            reflection.source_ids = sorted(set(reflection.source_ids) | {
                sid for oid in support | counter if oid in all_nodes for sid in all_nodes[oid].source_ids})
            reflection.span_ids = sorted(set(reflection.span_ids) | {
                sid for oid in support | counter if oid in all_nodes for sid in all_nodes[oid].span_ids})
            independent = {sid for oid in support if oid in all_nodes for sid in all_nodes[oid].source_ids}
            reflection.confidence = (1 + len(independent)) / (2 + len(independent) + 2 * len(counter))
            reflection.state = ("revised" if any(e.kind == "CORRECTS" and e.target == reflection.id
                                                 for e in relations.values()) else
                                "challenged" if counter else "supported" if len(independent) >= 2 else "hypothesis")
            output[reflection.id] = reflection

    if consolidate:
        for key, group in by_key.items():
            if len(group) < 2:
                continue
            # Extractive navigation retains every source and critical assertion. Never replaces L0/L1.
            group = sorted(group, key=lambda n: (n.generation, n.id))[-128:]
            source_ids = sorted({sid for n in group for sid in n.source_ids})
            span_ids = sorted({sid for n in group for sid in n.span_ids})
            summary = Node(stable_id("summary", key, [n.id for n in group]), "summary", group[0].subject,
                           group[0].predicate, "\n".join(n.value for n in group), source_ids, span_ids,
                           scope=group[0].scope, tier="scenario", state="supported",
                           protected=any(n.protected for n in group), support_ids=[n.id for n in group])
            output[summary.id] = summary
            for node in group:
                node.tier = "long_term"
                output[node.id] = node
    return list(output.values()), list(relations.values())
