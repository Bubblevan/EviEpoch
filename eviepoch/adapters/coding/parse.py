"""Parse AML string trajectories into engineering artifacts (TRD §19.1, §19.3)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from ...domain import Source, locate, stable_id
from .lexical import coding_terms
from .models import ObservationKind
from .snapshot import dirty_overlay_hash, repo_id, snapshot_id

_DIFF = re.compile(r"(^diff --git .+$|^--- .+$|^\+\+\+ .+$)", re.M)
_OBS_TYPE = re.compile(
    r"(?:(?:type|observation(?:\s*type)?)\s*[:=]\s*(bugfix|feature|refactor|change|discovery|decision))",
    re.I,
)
_FILES_MOD = re.compile(r"files?\s*modified\s*[:=]\s*([^\n]+)", re.I)
_FILES_READ = re.compile(r"files?\s*read\s*[:=]\s*([^\n]+)", re.I)
_TITLE = re.compile(r"(?:title|summary)\s*[:=]\s*([^\n]{3,200})", re.I)


@dataclass
class ParsedMessage:
    repo_remote: str | None = None
    project_label: str | None = None
    commit_sha: str | None = None
    tree_sha: str | None = None
    parent_shas: list[str] = field(default_factory=list)
    cwd: str | None = None
    tool_name: str | None = None
    diff_text: str | None = None
    test_command: str | None = None
    exit_code: int | None = None
    test_output: str | None = None
    observation_kind: ObservationKind = "unknown"
    observation_title: str = ""
    facts: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    decision: str | None = None
    problem: str | None = None
    paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


def _split_paths(raw: str) -> list[str]:
    parts = re.split(r"[,;]\s*|\s+", raw.strip())
    return [part for part in parts if part and "/" in part or "." in part][:64]


def _json_tool_payload(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(text[start:])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_source(source: Source) -> ParsedMessage:
    text = source.content
    parsed = ParsedMessage()
    locator = source.locator or {}
    parsed.cwd = locator.get("cwd") if isinstance(locator.get("cwd"), str) else None
    parsed.tool_name = locator.get("tool_name") if isinstance(locator.get("tool_name"), str) else None

    for match in re.finditer(r"(?:repo(?:sitory)?|remote|project)\s*[:=]\s*(\S+)", text, re.I):
        label = match.group(1).strip()
        if "://" in label or label.count("/") >= 2:
            parsed.repo_remote = label
        else:
            parsed.project_label = label
    for match in re.finditer(r"(?:commit|sha)\s*[:=]\s*([0-9a-f]{7,40})", text, re.I):
        parsed.commit_sha = match.group(1).lower()
    for match in re.finditer(r"tree\s*[:=]\s*([0-9a-f]{7,40})", text, re.I):
        parsed.tree_sha = match.group(1).lower()
    parent_shas: list[str] = []
    for match in re.finditer(r"parent(?:s)?\s*[:=]\s*([0-9a-f,\s]+)", text, re.I):
        parent_shas.extend(token for token in re.findall(r"[0-9a-f]{7,40}", match.group(1).lower()))
    if parent_shas:
        parsed.parent_shas = list(dict.fromkeys(parent_shas))

    if _DIFF.search(text):
        start = text.find("diff --git")
        parsed.diff_text = text[start:] if start >= 0 else text
        for line in parsed.diff_text.splitlines()[:40]:
            if line.startswith("+++ b/"):
                parsed.files_modified.append(line[6:].strip())
            elif line.startswith("--- a/"):
                path = line[6:].strip()
                if path != "/dev/null":
                    parsed.files_read.append(path)

    if re.search(r"\bpytest\b|\bnpm test\b|\bgo test\b|\bcargo test\b|\bjest\b", text, re.I):
        parsed.test_command = next(
            (line.strip() for line in text.splitlines()
             if re.search(r"\b(pytest|npm test|go test|cargo test|jest)\b", line, re.I)),
            "test",
        )
    exit_match = re.search(r"(?:exit(?:\s*code)?|returned)\s*[:=]?\s*(-?\d+)", text, re.I)
    if exit_match:
        parsed.exit_code = int(exit_match.group(1))
    if re.search(r"\b(FAILED|PASSED|ERROR)\b", text):
        parsed.test_output = text

    type_match = _OBS_TYPE.search(text)
    if type_match:
        parsed.observation_kind = type_match.group(1).lower()  # type: ignore[assignment]
    title_match = _TITLE.search(text)
    if title_match:
        parsed.observation_title = title_match.group(1).strip()
    mod_match = _FILES_MOD.search(text)
    if mod_match:
        parsed.files_modified.extend(_split_paths(mod_match.group(1)))
    read_match = _FILES_READ.search(text)
    if read_match:
        parsed.files_read.extend(_split_paths(read_match.group(1)))

    data = _json_tool_payload(text)
    if data:
        if isinstance(data.get("tool"), str):
            parsed.tool_name = data["tool"]
        if isinstance(data.get("cwd"), str):
            parsed.cwd = data["cwd"]
        if isinstance(data.get("commit"), str):
            parsed.commit_sha = data["commit"].lower()
        if isinstance(data.get("repository"), str):
            parsed.repo_remote = data["repository"]
        obs = data.get("observation")
        if isinstance(obs, dict):
            if isinstance(obs.get("type"), str):
                parsed.observation_kind = obs["type"].lower()  # type: ignore[assignment]
            if isinstance(obs.get("title"), str):
                parsed.observation_title = obs["title"]
            facts = obs.get("facts")
            if isinstance(facts, list):
                parsed.facts.extend(str(item) for item in facts if isinstance(item, str))
            for key, bucket in (("files_read", parsed.files_read), ("files_modified", parsed.files_modified)):
                values = obs.get(key)
                if isinstance(values, list):
                    bucket.extend(str(item) for item in values if isinstance(item, str))

    parsed.paths = coding_terms(text)
    parsed.symbols = [term for term in parsed.paths if term[0].isupper() or "." in term][:32]

    if source.role == "assistant" and not parsed.observation_title:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        if 3 <= len(first_line) <= 200:
            parsed.observation_title = first_line
    if source.role == "assistant" and "decided" in text.lower():
        parsed.decision = parsed.observation_title or text[:400]
    if "root cause" in text.lower() or "because" in text.lower():
        parsed.problem = text[:800]

    parsed.files_read = list(dict.fromkeys(parsed.files_read))
    parsed.files_modified = list(dict.fromkeys(parsed.files_modified))
    return parsed


def span_for_quote(source: Source, quote: str):
    return locate(source, quote)


def patch_hash(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def resolve_repo(parsed: ParsedMessage, namespace: str) -> tuple[str, str | None, str | None]:
    rid = repo_id(namespace, parsed.repo_remote, parsed.project_label, parsed.cwd)
    return rid, parsed.repo_remote, parsed.project_label


def resolve_snapshot(namespace: str, repo: str, parsed: ParsedMessage, diff_text: str | None):
    overlay = dirty_overlay_hash(diff_text)
    sid, provisional = snapshot_id(
        namespace,
        repo,
        commit_sha=parsed.commit_sha,
        tree_sha=parsed.tree_sha,
        overlay=overlay,
    )
    return sid, provisional, overlay, parsed.commit_sha, parsed.tree_sha
