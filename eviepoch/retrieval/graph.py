"""Bounded, independent entity/relation candidate recall."""
from __future__ import annotations

import re

from ..domain import Snapshot
from .scoring import overlap


_CAPITALIZED = re.compile(r"(?<![\w])[A-Z][A-Za-z0-9.+#_-]*(?:\s+[A-Z][A-Za-z0-9.+#_-]*)*")
_STOP_ENTITIES = {"a", "an", "the", "which", "who", "what", "when", "where", "why", "how",
                  "this", "that", "these", "those", "i", "we", "you"}


def query_entities(query: str, planned: list[str]) -> list[str]:
    """Use planner entities plus a small proper-name fallback for planner omissions."""
    values = []
    seen = set()
    for value in [*planned, *_CAPITALIZED.findall(query)]:
        value = " ".join(value.split()).strip(" .,:;!?()[]{}\"'")
        key = value.casefold()
        if not value or key in _STOP_ENTITIES or len(value) > 128 or key in seen:
            continue
        seen.add(key)
        values.append(value)
        if len(values) == 32:
            break
    return values


def retrieve(view, query, plan, config) -> dict[str, float]:
    """Return a ranked graph arm; final fusion and evidence checks stay in the engine."""
    if not config.graph_retrieval_enabled:
        return {}
    entities = query_entities(query, plan.entities)
    if not entities and not plan.attributes:
        return {}
    limit = min(config.graph_budget, max(config.candidate_limit, config.candidate_limit * 4))
    return view.graph_candidates(entities, plan.attributes, limit, config.graph_depth, query=query)


def _source_ids_for_objects(ids, snap: Snapshot) -> set[str]:
    result = set()
    for oid in ids:
        if oid in snap.sources:
            result.add(oid)
        elif oid in snap.spans:
            source_id = snap.spans[oid].source_id
            if source_id in snap.sources:
                result.add(source_id)
        elif oid in snap.nodes:
            result.update(sid for sid in snap.nodes[oid].source_ids if sid in snap.sources)
    return result


def _relation_candidate_channel(snap: Snapshot, seed_ids: set[str], query: str,
                               graph_depth: int) -> dict[str, float]:
    """Rank graph-expanded objects as a separate RRF arm before cross-encoder scoring."""
    seed_nodes = seed_ids & snap.nodes.keys()
    seed_sources = _source_ids_for_objects(seed_ids, snap)
    seed_keys = {snap.nodes[nid].key for nid in seed_nodes}
    priorities: dict[str, float] = {}
    visited = set(seed_nodes)
    frontier = set(seed_nodes)
    edge_weight = {
        "REQUIRES": 1.4, "EXCEPT_IF": 1.6, "RULE_VERSION": 1.4,
        "CONTRADICTS": 1.8, "CORRECTS": 1.8, "RETRACTS": 1.8,
        "SUPERSEDES": 1.5,
    }
    for depth in range(graph_depth):
        next_frontier = set()
        for edge in snap.edges:
            endpoints = {edge.origin, edge.target}
            if not endpoints & frontier:
                continue
            base = max(0.5, 2.5 - depth) + edge_weight.get(edge.kind, 0.0)
            for nid in endpoints - seed_nodes:
                if nid in snap.nodes:
                    priorities[nid] = max(priorities.get(nid, 0.0), base)
                    if nid not in visited:
                        next_frontier.add(nid)
            for sid in edge.source_ids:
                priorities[sid] = max(priorities.get(sid, 0.0), base)
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break

    # Same-subject/property peers are returned by related() even where the store
    # has no explicit edge row. Keep them in the relation arm as state candidates.
    for node in snap.nodes.values():
        if node.id not in seed_nodes and node.key in seed_keys:
            priorities[node.id] = max(priorities.get(node.id, 0.0), 3.0)
    for oid, node in snap.nodes.items():
        if oid not in priorities:
            continue
        priority = priorities[oid] + 1.5 * overlap(query, node.representation())
        priorities[oid] = priority
        for sid in node.source_ids:
            priorities[sid] = max(priorities.get(sid, 0.0), priority)

    for sid in seed_sources:
        priorities.pop(sid, None)
    return dict(sorted(priorities.items(), key=lambda row: (-row[1], row[0])))


def _source_ids_for_objects(ids, snap: Snapshot) -> set[str]:
    result = set()
    for oid in ids:
        if oid in snap.sources:
            result.add(oid)
        elif oid in snap.spans:
            source_id = snap.spans[oid].source_id
            if source_id in snap.sources:
                result.add(source_id)
        elif oid in snap.nodes:
            result.update(sid for sid in snap.nodes[oid].source_ids if sid in snap.sources)
    return result


def _relation_candidate_channel(snap: Snapshot, seed_ids: set[str], query: str,
                               graph_depth: int) -> dict[str, float]:
    """Rank graph-expanded objects as a separate RRF arm before cross-encoder scoring."""
    seed_nodes = seed_ids & snap.nodes.keys()
    seed_sources = _source_ids_for_objects(seed_ids, snap)
    seed_keys = {snap.nodes[nid].key for nid in seed_nodes}
    priorities: dict[str, float] = {}
    visited = set(seed_nodes)
    frontier = set(seed_nodes)
    edge_weight = {
        "REQUIRES": 1.4, "EXCEPT_IF": 1.6, "RULE_VERSION": 1.4,
        "CONTRADICTS": 1.8, "CORRECTS": 1.8, "RETRACTS": 1.8,
        "SUPERSEDES": 1.5,
    }
    for depth in range(graph_depth):
        next_frontier = set()
        for edge in snap.edges:
            endpoints = {edge.origin, edge.target}
            if not endpoints & frontier:
                continue
            base = max(0.5, 2.5 - depth) + edge_weight.get(edge.kind, 0.0)
            for nid in endpoints - seed_nodes:
                if nid in snap.nodes:
                    priorities[nid] = max(priorities.get(nid, 0.0), base)
                    if nid not in visited:
                        next_frontier.add(nid)
            for sid in edge.source_ids:
                priorities[sid] = max(priorities.get(sid, 0.0), base)
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break

    # Same-subject/property peers are returned by related() even where the store
    # has no explicit edge row. Keep them in the relation arm as state candidates.
    for node in snap.nodes.values():
        if node.id not in seed_nodes and node.key in seed_keys:
            priorities[node.id] = max(priorities.get(node.id, 0.0), 3.0)
    for oid, node in snap.nodes.items():
        if oid not in priorities:
            continue
        priority = priorities[oid] + 1.5 * overlap(query, node.representation())
        priorities[oid] = priority
        for sid in node.source_ids:
            priorities[sid] = max(priorities.get(sid, 0.0), priority)

    for sid in seed_sources:
        priorities.pop(sid, None)
    return dict(sorted(priorities.items(), key=lambda row: (-row[1], row[0])))
