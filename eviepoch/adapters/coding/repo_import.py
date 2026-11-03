"""Import one explicitly authorized Git repository (TRD §19.1).

Git is pinned to that repository's own .git. A parent work tree is not consulted,
so the process that runs EviEpoch cannot substitute its checkout for the task.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ...domain import Source, stable_id
from .ingest import ingest_sources
from ..base import IngestExtension


class UnauthorizedRepository(ValueError):
    """The path is not itself an authorized repository."""


def _git(repo: Path, *args: str, optional: bool = False) -> str:
    git_dir = repo / ".git"
    if not git_dir.exists():
        raise UnauthorizedRepository(f"{repo} has no .git; parent repositories are not used")
    env = os.environ.copy()
    env["GIT_DIR"] = str(git_dir)
    env["GIT_WORK_TREE"] = str(repo)
    completed = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        if optional:
            return ""
        raise UnauthorizedRepository(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def capture_authorized_repository(repo_path: Path) -> str:
    repo = Path(repo_path).resolve()
    head = _git(repo, "rev-parse", "HEAD").strip()
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}").strip()
    parents = [item for item in _git(repo, "rev-parse", "--verify", f"{head}^@", optional=True).split() if item]
    remote = _git(repo, "remote", "get-url", "origin", optional=True).strip()
    dirty = _git(repo, "diff", "HEAD")
    lines = [
        "source: authorized-repo",
        "tool: repo-import",
        "client: repo-import",
        f"authorized_path: {repo.as_posix()}",
    ]
    if remote:
        lines.append(f"repository: {remote}")
    lines.append(f"commit: {head}")
    lines.append(f"tree: {tree}")
    if parents:
        lines.append("parents: " + ",".join(parents))
    lines.extend(_ancestry_lines(repo))
    for row in _git(repo, "ls-files", "-s").splitlines():
        mode, blob, rest = row.split(" ", 2)
        _stage, path = rest.split("\t", 1)
        _ = mode
        lines.append(f"blob {blob} {path}")
    transcript = "\n".join(lines) + "\n"
    if dirty.strip():
        if not dirty.endswith("\n"):
            dirty += "\n"
        transcript += "uncommitted:\n" + dirty
    return transcript


def committed_file_bodies(repo_path: Path) -> dict[str, str]:
    """Read committed file bytes from this repository only."""
    repo = Path(repo_path).resolve()
    bodies: dict[str, str] = {}
    for row in _git(repo, "ls-files", "-s").splitlines():
        _mode, _blob, rest = row.split(" ", 2)
        _stage, path = rest.split("\t", 1)
        raw = _git(repo, "show", f"HEAD:{path}", optional=True)
        if "\0" in raw or len(raw) > 262144:
            continue
        bodies[path] = raw
    return bodies


def _ancestry_lines(repo: Path) -> list[str]:
    raw = _git(repo, "log", "--all", "--pretty=format:%H%x09%T%x09%P", optional=True)
    lines: list[str] = []
    for index, row in enumerate(raw.splitlines()):
        if index >= 512:
            break
        sha, tree, parents = (row.split("\t") + ["", ""])[:3]
        sha = sha.strip().lower()
        if not sha:
            continue
        parent_shas = [item.lower() for item in parents.split() if item]
        line = f"ancestry {sha}"
        if tree.strip():
            line += f" tree {tree.strip().lower()}"
        if parent_shas:
            line += " " + " ".join(parent_shas)
        lines.append(line)
        message = _git(repo, "log", "-1", "--format=%B", sha, optional=True)
        reverted = re.search(r"This reverts commit ([0-9a-f]{7,40})", message or "", re.I)
        if reverted:
            lines.append(f"reverts {sha} {reverted.group(1).lower()}")
    return lines


def import_authorized_repository(namespace: str, repo_path: Path, session_id: str, source_id: str) -> tuple[Source, IngestExtension]:
    repo = Path(repo_path).resolve()
    transcript = capture_authorized_repository(repo)
    source = Source(
        source_id,
        namespace,
        stable_id("repo-import", source_id),
        session_id,
        0,
        "tool",
        transcript,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
        locator={"file_bodies": committed_file_bodies(repo)},
    )
    return source, ingest_sources([source])
