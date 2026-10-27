"""A correction proposal cannot invalidate history without a second, targeted check."""
from dataclasses import replace

from ..domain import InvalidExtraction, stable_id


def verify_changes(nodes, edges, sources, known, providers):
    proposed = [n for n in nodes if n.change in {"correction", "retraction"}]
    if not proposed:
        return nodes, edges
    for node in proposed:
        node.change_proposal = node.change
    review = getattr(providers, "verify_changes", None)
    data = review(proposed, sources, known) if review else {"decisions": []}
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        raise InvalidExtraction("temporal review requires decisions")
    available = {**known.nodes, **{n.id: n for n in nodes}}
    indexed = {}
    for row in decisions:
        if not isinstance(row, dict) or row.get("id") not in {n.id for n in proposed} or row["id"] in indexed:
            raise InvalidExtraction("invalid temporal review ID")
        if row.get("classification") not in {"correction", "retraction", "change", "uncertain"}:
            raise InvalidExtraction("invalid temporal review classification")
        refs = row.get("target_ids", [])
        if not isinstance(refs, list) or any(not isinstance(oid, str) or oid not in available for oid in refs):
            raise InvalidExtraction("temporal review invented a target")
        indexed[row["id"]] = row
    for node in proposed:
        row = indexed.get(node.id, {"classification": "uncertain", "target_ids": []})
        classification = row["classification"]
        targets = [available[oid] for oid in row.get("target_ids", [])]
        compatible = (bool(targets) and node.modality not in {"planned", "hypothetical"} and
                      all(t.id != node.id and t.kind != "summary" and t.key == node.key and t.modality == node.modality and
                          not (node.valid_time.earliest and t.valid_time.latest and
                               node.valid_time.earliest > t.valid_time.latest) for t in targets))
        node.change_verified = classification in {"correction", "retraction"} and compatible
        node.change_targets = sorted(t.id for t in targets) if node.change_verified else []
        node.change = classification if node.change_verified or classification == "change" else "assertion"
    active = {n.id: n for n in nodes}
    checked = []
    for edge in edges:
        if edge.kind in {"CORRECTS", "RETRACTS"}:
            origin = active.get(edge.origin)
            verified = origin and origin.change_verified and edge.target in origin.change_targets
            kind = edge.kind if verified else "CONTRADICTS"
            edge = replace(edge, id=stable_id(edge.origin, edge.target, kind), kind=kind,
                           derivation="reviewed_change" if verified else "unverified_change_proposal")
        checked.append(edge)
    return nodes, checked


def project_state_edges(snapshot):
    """Persisted unreviewed correction edges also remain non-destructive on reads."""
    result = []
    for edge in snapshot.edges:
        if edge.kind in {"CORRECTS", "RETRACTS"}:
            origin = snapshot.nodes.get(edge.origin)
            if not origin or not origin.change_verified or edge.target not in origin.change_targets:
                edge = replace(edge, kind="CONTRADICTS", derivation="unverified_change_proposal")
        result.append(edge)
    snapshot.edges = result
    return snapshot
