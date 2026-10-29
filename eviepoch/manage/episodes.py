"""Extractive episode navigation. It discovers sources but never acts as proof."""
from collections import defaultdict

from ..domain import Node, stable_id


def episodes(sources, nodes):
    groups = defaultdict(list)
    for source in sources:
        groups[source.session_id].append(source)
    result = []
    for session, group in groups.items():
        ids = {s.id for s in group}
        atoms = [n for n in nodes if n.kind not in {"summary", "episode"} and set(n.source_ids) <= ids]
        excerpts = [f"{n.subject} / {n.predicate}: {n.value[:400]}" for n in sorted(atoms, key=lambda n: (not n.protected, n.id))]
        if not excerpts:
            excerpts = [f"{s.role}: {s.content[:240]} {s.content[-240:]}" for s in sorted(group, key=lambda s: s.ordinal)]
        value = "\n".join(excerpts)[:12000]
        result.append(Node(stable_id("episode-v1", sorted(ids)), "episode", "session:" + session,
                           "source_navigation", value, sorted(ids), sorted({sid for n in atoms for sid in n.span_ids}),
                           scope=session, tier="scenario", state="navigation", support_ids=sorted(n.id for n in atoms),
                           protected=any(n.protected for n in atoms)))
    return result
