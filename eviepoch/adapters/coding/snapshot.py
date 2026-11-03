"""Repository snapshot identity (TRD §19.2)."""
from __future__ import annotations

import hashlib

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


def graph_from_payloads(
    payloads: list[dict],
) -> tuple[dict[str, tuple[str, ...]], dict[str, str], dict[str, str]]:
    """Rebuild commit parents, revert targets, and trees from stored snapshot payloads."""
    parents: dict[str, tuple[str, ...]] = {}
    reverts: dict[str, str] = {}
    trees: dict[str, str] = {}
    for payload in payloads:
        sha = payload.get("commit_sha")
        if not isinstance(sha, str) or not sha:
            continue
        recorded = tuple(item for item in (payload.get("parent_shas") or []) if isinstance(item, str) and item)
        if sha not in parents or len(recorded) > len(parents[sha]):
            parents[sha] = recorded
        reverted = payload.get("reverts")
        if isinstance(reverted, str) and reverted:
            reverts[sha] = reverted
        tree = payload.get("tree_sha")
        if isinstance(tree, str) and tree:
            trees[sha] = tree
    return parents, reverts, trees


def _ancestors(commit: str, parents: dict[str, tuple[str, ...]], limit: int = 256) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(commit, ()))
    while stack and len(seen) < limit:
        item = stack.pop()
        if not item or item in seen:
            continue
        seen.add(item)
        stack.extend(parents.get(item, ()))
    return seen


def _tree_rollback(
    left: str,
    right: str,
    parents: dict[str, tuple[str, ...]],
    trees: dict[str, str],
) -> bool:
    """True when left's tree returns to ancestor right and no immediate parent still has that tree."""
    if not trees.get(left) or trees.get(left) != trees.get(right):
        return False
    if right not in _ancestors(left, parents):
        return False
    immediate = parents.get(left, ())
    if not immediate or any(not trees.get(parent) for parent in immediate):
        return False
    return trees[left] not in {trees[parent] for parent in immediate}


def ancestry_relation(
    left: str | None,
    right: str | None,
    parents: dict[str, tuple[str, ...]] | None = None,
    reverts: dict[str, str] | None = None,
    trees: dict[str, str] | None = None,
) -> str:
    """Classify left against right from stored parents. Commit time is not a relationship.

    same: identical SHA. ancestor / descendant: one stored parent chain reaches the other.
    fork: a stored ancestor is shared and neither contains the other. merge: left has at
    least two stored parents and right is one of them. rollback: left records a revert of
    right, or its stored tree returns to right. Otherwise unknown — differing SHAs are
    not an ancestor link.
    """
    if not left or not right:
        return "unknown"
    if left == right:
        return "same"
    stored = parents or {}
    reverted = reverts or {}
    stored_trees = trees or {}
    if reverted.get(left) == right or _tree_rollback(left, right, stored, stored_trees):
        return "rollback"
    immediate = stored.get(left, ())
    if right in immediate and len(immediate) >= 2:
        return "merge"
    if right in _ancestors(left, stored):
        return "descendant"
    if left in _ancestors(right, stored):
        return "ancestor"
    if _ancestors(left, stored) & _ancestors(right, stored):
        return "fork"
    return "unknown"
