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
    uncommitted_diff: str | None = None
    diff_verbatim: bool = False
    uncommitted_verbatim: bool = False
    client: str | None = None
    tool_call_id: str | None = None
    authorized_path: str | None = None
    file_hashes: dict[str, str] = field(default_factory=dict)
    commit_graph: list[dict] = field(default_factory=list)
    reverts: str | None = None


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


def exact_diff(text: str) -> str | None:
    """Return the diff slice exactly as it appears, without the following command or prose."""
    start = text.find("diff --git ")
    if start < 0:
        return None
    kept: list[str] = []
    for index, line in enumerate(text[start:].splitlines(keepends=True)):
        body = line.rstrip("\r\n")
        if index == 0 or body.startswith(("diff --git ", "index ", "--- ", "+++ ", "@@ ", "\\", "+", "-", " ")):
            kept.append(line)
            continue
        break
    return "".join(kept) if kept else None


def parse_source(source: Source) -> ParsedMessage:
    text = source.content
    parsed = ParsedMessage()
    locator = source.locator or {}
    parsed.cwd = locator.get("cwd") if isinstance(locator.get("cwd"), str) else None
    cwd_line = re.search(r"(?m)^cwd\s*:\s*(\S+)\s*$", text)
    if cwd_line and not parsed.cwd:
        parsed.cwd = cwd_line.group(1)
    parsed.tool_name = locator.get("tool_name") if isinstance(locator.get("tool_name"), str) else None

    for match in re.finditer(r"(?:repo(?:sitory)?|remote|project)\s*[:=]\s*(\S+)", text, re.I):
        label = match.group(1).strip()
        if _is_remote(label):
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

    uncommitted_at = text.find("uncommitted:\n")
    uncommitted_region = text[uncommitted_at + len("uncommitted:\n"):] if uncommitted_at >= 0 else ""
    committed_region = text[:uncommitted_at] if uncommitted_at >= 0 else text
    parsed.diff_text = exact_diff(committed_region)
    parsed.uncommitted_diff = exact_diff(uncommitted_region) if uncommitted_region else None
    for diff_text in (parsed.diff_text, parsed.uncommitted_diff):
        if not diff_text:
            continue
        for line in diff_text.splitlines()[:40]:
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
        matched = [line for line in text.splitlines(keepends=True) if re.search(r"\b(FAILED|PASSED|ERROR)\b", line)]
        parsed.test_output = "".join(matched) if matched else None

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
        if isinstance(data.get("tree"), str):
            parsed.tree_sha = data["tree"].lower()
        parents = data.get("parents")
        if isinstance(parents, list):
            parsed.parent_shas = list(dict.fromkeys(
                str(item).lower() for item in parents if isinstance(item, str)
            ))
        if isinstance(data.get("reverts"), str) and re.fullmatch(r"[0-9a-f]{7,40}", data["reverts"].lower()):
            parsed.reverts = data["reverts"].lower()
        if isinstance(data.get("repository"), str):
            parsed.repo_remote = data["repository"]
        if isinstance(data.get("diff"), str) and data["diff"]:
            parsed.diff_text = data["diff"]
            _note_diff_paths(parsed, data["diff"])
        if isinstance(data.get("uncommitted_diff"), str) and data["uncommitted_diff"]:
            parsed.uncommitted_diff = data["uncommitted_diff"]
            _note_diff_paths(parsed, data["uncommitted_diff"])
        if isinstance(data.get("exit_code"), int) and not isinstance(data.get("exit_code"), bool):
            parsed.exit_code = data["exit_code"]
        if isinstance(data.get("client"), str):
            parsed.client = data["client"]
        if isinstance(data.get("tool_call_id"), str):
            parsed.tool_call_id = data["tool_call_id"]
        if isinstance(data.get("command"), str) and data["command"].strip():
            parsed.test_command = data["command"].strip()
        if isinstance(data.get("output"), str):
            parsed.test_output = data["output"]
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

    if re.search(r"(?m)^tool:\s*repo-import\s*$", text):
        parsed.tool_name = parsed.tool_name or "repo-import"
    client_match = re.search(r"(?m)^client:\s*(\S+)\s*$", text)
    if client_match and not parsed.client:
        parsed.client = client_match.group(1)
    call_match = re.search(r"(?m)^tool_call_id:\s*(\S+)\s*$", text)
    if call_match and not parsed.tool_call_id:
        parsed.tool_call_id = call_match.group(1)
    authorized = re.search(r"(?m)^authorized_path:\s*(\S+)\s*$", text)
    if authorized:
        parsed.authorized_path = authorized.group(1)
    for match in re.finditer(r"(?m)^blob\s+([0-9a-f]{7,64})\s+(\S+)\s*$", text):
        parsed.file_hashes[match.group(2)] = match.group(1).lower()
    parsed.commit_graph = _commit_graph(text)
    if parsed.commit_sha:
        recorded = next((item for item in parsed.commit_graph if item["sha"] == parsed.commit_sha), None)
        if recorded and recorded.get("reverts") and not parsed.reverts:
            parsed.reverts = recorded["reverts"]
    if parsed.commit_sha and not parsed.reverts:
        message_revert = re.search(r"This reverts commit ([0-9a-f]{7,40})", text, re.I)
        if message_revert:
            parsed.reverts = message_revert.group(1).lower()
    parsed.diff_verbatim = bool(parsed.diff_text and parsed.diff_text in text)
    parsed.uncommitted_verbatim = bool(parsed.uncommitted_diff and parsed.uncommitted_diff in text)

    parsed.files_read = list(dict.fromkeys(parsed.files_read))
    parsed.files_modified = list(dict.fromkeys(parsed.files_modified))
    return parsed


def _commit_graph(text: str) -> list[dict]:
    graph: dict[str, dict] = {}
    for match in re.finditer(
        r"(?m)^ancestry\s+([0-9a-f]{7,40})(?:\s+tree\s+([0-9a-f]{7,40}))?((?:\s+[0-9a-f]{7,40})*)\s*$",
        text,
        re.I,
    ):
        sha = match.group(1).lower()
        parents = re.findall(r"[0-9a-f]{7,40}", (match.group(3) or "").lower())
        graph[sha] = {
            "sha": sha,
            "tree": match.group(2).lower() if match.group(2) else None,
            "parents": parents,
            "reverts": None,
        }
    for match in re.finditer(r"(?m)^reverts\s+([0-9a-f]{7,40})\s+([0-9a-f]{7,40})\s*$", text, re.I):
        sha = match.group(1).lower()
        item = graph.setdefault(sha, {"sha": sha, "tree": None, "parents": [], "reverts": None})
        item["reverts"] = match.group(2).lower()
    return list(graph.values())


def _note_diff_paths(parsed: ParsedMessage, diff_text: str) -> None:
    for line in diff_text.splitlines()[:40]:
        if line.startswith("+++ b/"):
            parsed.files_modified.append(line[6:].strip())
        elif line.startswith("--- a/"):
            path = line[6:].strip()
            if path != "/dev/null":
                parsed.files_read.append(path)


def _is_remote(label: str) -> bool:
    return "://" in label or label.count("/") >= 2 or (label.startswith("git@") and ":" in label)


def span_for_quote(source: Source, quote: str):
    return locate(source, quote)


def patch_hash(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def resolve_repo(parsed: ParsedMessage, namespace: str) -> tuple[str, str | None, str | None]:
    if parsed.authorized_path and not parsed.repo_remote:
        return stable_id("coding-repo-v1", namespace, "authorized-path", parsed.authorized_path), None, parsed.authorized_path
    rid = repo_id(namespace, parsed.repo_remote, parsed.project_label, parsed.cwd)
    return rid, parsed.repo_remote, parsed.project_label


def resolve_snapshot(namespace: str, repo: str, parsed: ParsedMessage):
    """Overlay comes only from an uncommitted diff. A recorded patch does not create a second snapshot."""
    overlay = dirty_overlay_hash(parsed.uncommitted_diff)
    sid, provisional = snapshot_id(
        namespace,
        repo,
        commit_sha=parsed.commit_sha,
        tree_sha=parsed.tree_sha,
        overlay=overlay,
    )
    clean_id, _ = snapshot_id(
        namespace,
        repo,
        commit_sha=parsed.commit_sha,
        tree_sha=parsed.tree_sha,
        overlay=None,
    )
    return sid, provisional, overlay, parsed.commit_sha, parsed.tree_sha, clean_id
