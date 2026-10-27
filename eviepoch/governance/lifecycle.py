"""Apply governance inside the same transaction as Add publication."""
from ..domain import Governance, stable_id
import json
from .provenance import detach


def apply_policy(c, ns: str, policy: Governance, generation: int):
    if policy.action not in {"suppress", "erase", "deactivate"}:
        raise ValueError("unknown governance action")
    targets = set(policy.source_ids) | {policy.instruction_source_id}
    if policy.subject and policy.predicate:
        sql = "SELECT id FROM ee_objects WHERE ns=? AND subject_key=? AND predicate_key=? AND generation<=?"
        args = [ns, policy.subject.casefold(), policy.predicate.casefold(), generation]
        if policy.scope:
            sql += " AND scope_key=?"
            args.append(policy.scope.casefold())
        for oid, in c.execute(sql, args).fetchall():
            targets.update(r[0] for r in c.execute(
                "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?", (ns, oid)).fetchall())
    impacted = set(targets)
    for sid in targets:
        impacted.update(row[0] for row in c.execute(
            "SELECT object_id FROM ee_dependencies WHERE ns=? AND source_id=?", (ns, sid)).fetchall())
        c.execute("INSERT INTO ee_tombstones(ns,id,action,generation) VALUES(?,?,?,?) "
                  "ON CONFLICT(ns,id) DO UPDATE SET action=excluded.action,generation=excluded.generation",
                  (ns, sid, policy.action, generation))
    for oid in impacted:
        detach(c, ns, oid, targets)
        if policy.action == "erase":
            for table, column in (("ee_postings", "object_id"), ("ee_vectors", "id"),
                                  ("ee_edges", "id"), ("ee_objects", "id"),
                                  ("ee_dependencies", "object_id"), ("ee_provenance", "id"), ("ee_versions", "id")):
                c.execute(f"DELETE FROM {table} WHERE ns=? AND {column}=?", (ns, oid))
            c.execute("DELETE FROM ee_edges WHERE ns=? AND (origin=? OR target=?)", (ns, oid, oid))
        else:
            # Deactivation only changes active-working-set policy; direct retrieval remains possible.
            active = 2 if policy.action == "deactivate" else 0
            c.execute("UPDATE ee_objects SET active=? WHERE ns=? AND id=?", (active, ns, oid))
            if policy.action == "suppress":
                c.execute("DELETE FROM ee_vectors WHERE ns=? AND id=?", (ns, oid))
                c.execute("DELETE FROM ee_postings WHERE ns=? AND object_id=?", (ns, oid))
                c.execute("DELETE FROM ee_edges WHERE ns=? AND (id=? OR origin=? OR target=?)",
                          (ns, oid, oid, oid))
    # The instruction can name the forgotten value. It is suppressed even for deactivation.
    c.execute("UPDATE ee_objects SET active=0 WHERE ns=? AND id=?", (ns, policy.instruction_source_id))
    c.execute("INSERT INTO ee_operations(ns,id,generation,kind,payload) VALUES(?,?,?,?,?)",
              (ns, stable_id(ns, policy.instruction_source_id, policy.action), generation, policy.action,
               json.dumps({"source_ids": sorted(targets), "objects": len(impacted),
                           "backup_erasure": "external_copies_require_operator_confirmation"})))

