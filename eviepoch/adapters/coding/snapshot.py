"""Repository snapshot identity (TRD §19.2)."""
from __future__ import annotations

import hashlib
import json

from ...domain import digest, stable_id


IDENTITY_VERSION = 1


def dirty_overlay_hash(uncommitted_diff: str | None) -> str | None:
    if not uncommitted_diff or not uncommitted_diff.strip():
        return None
    return hashlib.sha256(uncommitted_diff.encode("utf-8")).hexdigest()


def snapshot_id(
    namespace: str,
    repo_id: str,
    *,
    commit_sha: str | None = None,
    tree_sha: str | None = None,
    overlay: str | None = None,
) -> tuple[str, bool]:
    """Return (snapshot_id, provisional). Missing SHA uses provisional snapshot."""
    provisional = not commit_sha and not tree_sha
    identity = digest(
        [
            "coding-snapshot-v1",
            namespace,
            repo_id,
            commit_sha or "unknown",
            tree_sha or "unknown",
            overlay or "",
            IDENTITY_VERSION,
        ]
    )
    return stable_id(identity), provisional


def repo_id(namespace: str, remote: str | None, project_label: str | None, basename_hint: str | None) -> str:
    """Folder basename alone is not unique identity."""
    if remote and remote.strip():
        return stable_id("coding-repo-v1", namespace, remote.strip().casefold())
    if project_label and project_label.strip():
        return stable_id("coding-repo-v1", namespace, "project", project_label.strip().casefold())
    if basename_hint and basename_hint.strip():
        return stable_id("coding-repo-v1", namespace, "provisional", basename_hint.strip().casefold())
    return stable_id("coding-repo-v1", namespace, "unknown")


def ancestry_relation(parent_sha: str | None, child_sha: str | None) -> str:
    if parent_sha and child_sha and parent_sha == child_sha:
        return "same"
    if parent_sha and child_sha:
        return "unknown_dag"
    return "unknown"
