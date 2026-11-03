"""Namespace-scoped media deletion. Bytes stay while any authorization remains."""
from __future__ import annotations

import json
from pathlib import Path


def multimodal_anchored(c, ns: str, oid: str) -> bool:
    row = c.execute("SELECT kind FROM ee_objects WHERE ns=? AND id=?", (ns, oid)).fetchone()
    if row and str(row[0]).startswith("multimodal_"):
        return True
    if c.execute("SELECT 1 FROM ee_media_assets WHERE ns=? AND asset_id=? LIMIT 1", (ns, oid)).fetchone():
        return True
    return bool(
        c.execute(
            "SELECT 1 FROM ee_media_occurrences WHERE ns=? AND occurrence_id=? LIMIT 1",
            (ns, oid),
        ).fetchone()
    )


def _paths_in(raw, found: list[str]) -> None:
    if not isinstance(raw, str):
        return
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    for key in ("storage_path", "crop_path"):
        path = data.get(key)
        if isinstance(path, str) and path and path not in found:
            found.append(path)


def collect_media_paths(c, ns: str, doomed: set[str]) -> list[str]:
    found: list[str] = []
    for oid in doomed:
        row = c.execute("SELECT payload FROM ee_objects WHERE ns=? AND id=?", (ns, oid)).fetchone()
        if row:
            _paths_in(row[0], found)
        stored = c.execute(
            "SELECT storage_path FROM ee_media_assets WHERE ns=? AND asset_id=?",
            (ns, oid),
        ).fetchone()
        if stored and isinstance(stored[0], str) and stored[0] and stored[0] not in found:
            found.append(stored[0])
    return found


def original_blocked_by_redaction(c, ns: str, asset_id: str) -> bool:
    """A mixed image is not returned until a traceable redaction is complete."""
    pending = False
    released = False
    rows = c.execute(
        "SELECT payload FROM ee_objects WHERE ns=? AND kind='multimodal_region' AND active>0",
        (ns,),
    ).fetchall()
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("asset_id") != asset_id and data.get("parent_asset_id") != asset_id:
            continue
        if data.get("redaction") == "complete" and data.get("disclosable") is True:
            released = True
        elif data.get("disclosable") is False:
            pending = True
    return pending and not released


def erase_multimodal_store(c, ns: str, doomed: set[str], paths: list[str]) -> None:
    """Drop this namespace's media rows, then bytes no remaining asset still names."""
    for oid in doomed:
        c.execute(
            "DELETE FROM ee_media_occurrences WHERE ns=? AND (occurrence_id=? OR source_id=?)",
            (ns, oid, oid),
        )
        c.execute("DELETE FROM ee_media_assets WHERE ns=? AND asset_id=?", (ns, oid))
    rows = c.execute("SELECT asset_id, payload FROM ee_media_assets WHERE ns=?", (ns,)).fetchall()
    for asset_id, raw in rows:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        kept = [sid for sid in data.get("source_ids") or [] if sid not in doomed]
        if kept == list(data.get("source_ids") or []):
            continue
        data["source_ids"] = kept
        if kept:
            c.execute(
                "UPDATE ee_media_assets SET payload=? WHERE ns=? AND asset_id=?",
                (json.dumps(data, ensure_ascii=False), ns, asset_id),
            )
            continue
        path = data.get("storage_path")
        c.execute("DELETE FROM ee_media_assets WHERE ns=? AND asset_id=?", (ns, asset_id))
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    for path in paths:
        if c.execute(
            "SELECT 1 FROM ee_media_assets WHERE ns=? AND storage_path=? LIMIT 1",
            (ns, path),
        ).fetchone():
            continue
        file = Path(path)
        if file.is_file():
            file.unlink()
