"""Canonical entity mention keys and transactional posting writes."""
from __future__ import annotations


def entity_key(value) -> str:
    """Conservative exact-name key; this is a navigation hint, not entity resolution."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split())[:256]


def index_entity_mentions(connection, ns: str, object_id: str, data: dict, *, pg: bool = False) -> None:
    """Index the subject, predicate, and object using the same contract for Add and migration."""
    marker = "%s" if pg else "?"
    mentions = (("subject", data.get("subject"), data.get("subject_id")),
                ("predicate", data.get("predicate"), data.get("predicate_id")),
                ("object", data.get("value"), data.get("value_id")))
    statement = ("INSERT INTO ee_entity_mentions(ns,entity_key,entity_id,object_id,role) "
                 f"VALUES({','.join([marker] * 5)}) ON CONFLICT DO NOTHING")
    for role, label, entity_id in mentions:
        key = entity_key(label)
        if key:
            connection.execute(statement, (ns, key, entity_key(entity_id) or None, object_id, role))
