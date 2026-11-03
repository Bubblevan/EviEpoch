"""Transitive coding closure for source deletion.

Erasing a message source also removes the snapshot, file, symbol, patch, test,
experience, edge, and blob rows that exist only because of that source. A coding
row still supported by another source in the same namespace stays. Blob bytes
shared with another namespace keep that namespace's row.
"""
from __future__ import annotations

import json

_PRIMARY_KEYS = (
    ("ee_repositories", "repo_id"),
    ("ee_code_snapshots", "snapshot_id"),
    ("ee_file_revisions", "file_id"),
    ("ee_symbol_revisions", "symbol_id"),
    ("ee_engineering_events", "event_id"),
    ("ee_patch_attempts", "attempt_id"),
    ("ee_test_runs", "test_run_id"),
    ("ee_experiences", "experience_id"),
    ("ee_coding_edges", "id"),
    ("ee_applicability_assessments", "assessment_id"),
)


def _coding_anchored(c, ns: str, oid: str) -> bool:
    row = c.execute("SELECT kind FROM ee_objects WHERE ns=? AND id=?", (ns, oid)).fetchone()
    if row and str(row[0]).startswith("coding_"):
        return True
    for table, column in _PRIMARY_KEYS:
        if c.execute(f"SELECT 1 FROM {table} WHERE ns=? AND {column}=? LIMIT 1", (ns, oid)).fetchone():
            return True
    return False


def _multimodal_anchored(c, ns: str, oid: str) -> bool:
    from .multimodal_closure import multimodal_anchored

    return multimodal_anchored(c, ns, oid)


def _dependency_sources(c, ns: str, oid: str) -> list[str]:
    return [
        row[0]
        for row in c.execute(
            "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?",
            (ns, oid),
        ).fetchall()
    ]


def _coding_child_ids(c, ns: str, oid: str) -> list[str]:
    found: list[str] = []
    queries = [
        ("SELECT file_id FROM ee_file_revisions WHERE ns=? AND snapshot_id=?", (ns, oid)),
        ("SELECT symbol_id FROM ee_symbol_revisions WHERE ns=? AND snapshot_id=?", (ns, oid)),
        ("SELECT symbol_id FROM ee_symbol_revisions WHERE ns=? AND file_id=?", (ns, oid)),
        ("SELECT attempt_id FROM ee_patch_attempts WHERE ns=? AND base_snapshot_id=?", (ns, oid)),
        ("SELECT test_run_id FROM ee_test_runs WHERE ns=? AND snapshot_id=?", (ns, oid)),
        ("SELECT id FROM ee_coding_edges WHERE ns=? AND (origin=? OR target=?)", (ns, oid, oid)),
        (
            "SELECT assessment_id FROM ee_applicability_assessments "
            "WHERE ns=? AND (query_snapshot_id=? OR historical_attempt_id=?)",
            (ns, oid, oid),
        ),
    ]
    if c.execute("SELECT 1 FROM ee_repositories WHERE ns=? AND repo_id=? LIMIT 1", (ns, oid)).fetchone():
        queries.append(("SELECT snapshot_id FROM ee_code_snapshots WHERE ns=? AND repo_id=?", (ns, oid)))
    for sql, args in queries:
        found.extend(row[0] for row in c.execute(sql, args).fetchall() if isinstance(row[0], str))
    return found


def doomed_ids(c, ns: str, targets: set[str]) -> set[str]:
    """Sources, direct dependents, and coding rows reachable only through them."""
    doomed = set(targets)
    changed = True
    while changed:
        changed = False
        for sid in list(doomed):
            rows = c.execute(
                "SELECT object_id FROM ee_dependencies WHERE ns=? AND source_id=?",
                (ns, sid),
            ).fetchall()
            for (oid,) in rows:
                if not isinstance(oid, str) or oid in doomed:
                    continue
                sources = _dependency_sources(c, ns, oid)
                if (_coding_anchored(c, ns, oid) or _multimodal_anchored(c, ns, oid)) and any(
                    source not in doomed for source in sources
                ):
                    continue
                doomed.add(oid)
                changed = True
            for child in _coding_child_ids(c, ns, sid):
                if child in doomed:
                    continue
                sources = _dependency_sources(c, ns, child)
                if sources and any(source not in doomed for source in sources):
                    continue
                doomed.add(child)
                changed = True
    return doomed


def shared_coding_ids(c, ns: str, targets: set[str], doomed: set[str]) -> set[str]:
    shared: set[str] = set()
    for sid in targets:
        rows = c.execute(
            "SELECT object_id FROM ee_dependencies WHERE ns=? AND source_id=?",
            (ns, sid),
        ).fetchall()
        for (oid,) in rows:
            if isinstance(oid, str) and oid not in doomed and _coding_anchored(c, ns, oid):
                shared.add(oid)
    return shared


def _harvest_blob_id(raw, found: set[str]) -> None:
    if not isinstance(raw, str) or "blob-" not in raw:
        return
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return
    blob_id = data.get("blob_id") if isinstance(data, dict) else None
    if isinstance(blob_id, str) and blob_id:
        found.add(blob_id)


def collect_blob_ids(c, ns: str, doomed: set[str]) -> set[str]:
    found: set[str] = set()
    for oid in doomed:
        row = c.execute("SELECT payload FROM ee_objects WHERE ns=? AND id=?", (ns, oid)).fetchone()
        if row:
            _harvest_blob_id(row[0], found)
        for table, column in _PRIMARY_KEYS:
            if table == "ee_coding_edges":
                continue
            row = c.execute(f"SELECT payload FROM {table} WHERE ns=? AND {column}=?", (ns, oid)).fetchone()
            if row:
                _harvest_blob_id(row[0], found)
    return found


def drop_coding_edges(c, ns: str, doomed: set[str]) -> None:
    for oid in doomed:
        c.execute(
            "DELETE FROM ee_coding_edges WHERE ns=? AND (id=? OR origin=? OR target=?)",
            (ns, oid, oid, oid),
        )


def erase_coding_store(c, ns: str, doomed: set[str], blob_ids: set[str]) -> None:
    for oid in doomed:
        for table, column in _PRIMARY_KEYS:
            c.execute(f"DELETE FROM {table} WHERE ns=? AND {column}=?", (ns, oid))
        c.execute("DELETE FROM ee_commit_parents WHERE ns=? AND snapshot_id=?", (ns, oid))
        c.execute(
            "DELETE FROM ee_applicability_assessments WHERE ns=? AND (query_snapshot_id=? OR historical_attempt_id=?)",
            (ns, oid, oid),
        )
        c.execute("DELETE FROM ee_file_revisions WHERE ns=? AND snapshot_id=?", (ns, oid))
        c.execute(
            "DELETE FROM ee_symbol_revisions WHERE ns=? AND (snapshot_id=? OR file_id=?)",
            (ns, oid, oid),
        )
        c.execute("DELETE FROM ee_patch_attempts WHERE ns=? AND base_snapshot_id=?", (ns, oid))
        c.execute("DELETE FROM ee_test_runs WHERE ns=? AND snapshot_id=?", (ns, oid))
    drop_coding_edges(c, ns, doomed)
    for blob_id in blob_ids:
        # Same bytes may still be authorized in another namespace. Only this ns is checked.
        remaining = c.execute(
            "SELECT COUNT(*) FROM ee_objects WHERE ns=? AND payload LIKE ?",
            (ns, f"%{blob_id}%"),
        ).fetchone()[0]
        if remaining:
            continue
        c.execute("DELETE FROM ee_coding_blobs WHERE ns=? AND blob_id=?", (ns, blob_id))
