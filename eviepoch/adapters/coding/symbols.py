"""Lightweight symbol extraction when full file bodies are unavailable."""
from __future__ import annotations

import hashlib
import re

from ...domain import stable_id
from .models import FileRevision, SymbolRevision

_PY = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)", re.M)
_JS = re.compile(r"(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()", re.M)
_GO = re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)", re.M)


def language_for_path(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(".py"):
        return "python"
    if lower.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "javascript"
    if lower.endswith(".go"):
        return "go"
    if lower.endswith(".rs"):
        return "rust"
    if lower.endswith(".java"):
        return "java"
    return None


def _tree_sitter_symbols(path: str, content: str, snapshot_id: str, file_id: str, source_ids: list[str]) -> list[SymbolRevision]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython
    except ImportError:
        return []
    lang = language_for_path(path)
    if lang != "python":
        return []
    language = Language(tspython.language())
    parser = Parser(language)
    tree = parser.parse(content.encode("utf-8"))
    symbols: list[SymbolRevision] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in {"function_definition", "class_definition"}:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = content[name_node.start_byte:name_node.end_byte]
            line = content.count("\n", 0, node.start_byte) + 1
            body = content[node.start_byte:node.end_byte]
            symbols.append(
                SymbolRevision(
                    stable_id("coding-symbol-v1", file_id, name, line),
                    snapshot_id,
                    file_id,
                    node.type,
                    name,
                    signature=body.split("\n", 1)[0][:240],
                    line_start=line,
                    line_end=content.count("\n", 0, node.end_byte) + 1,
                    body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    source_ids=list(source_ids),
                )
            )
        stack.extend(reversed(node.children))
    return symbols[:128]


def extract_symbols(path: str, content: str, snapshot_id: str, source_ids: list[str]) -> tuple[FileRevision, list[SymbolRevision]]:
    file_id = stable_id("coding-file-v1", snapshot_id, path)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
    file_rev = FileRevision(file_id, snapshot_id, path, language_for_path(path), content_hash, list(source_ids))
    lang = file_rev.language
    patterns = []
    if lang == "python":
        patterns.append(_PY)
    elif lang == "javascript":
        patterns.append(_JS)
    elif lang == "go":
        patterns.append(_GO)
    symbols: list[SymbolRevision] = []
    if content:
        symbols.extend(_tree_sitter_symbols(path, content, snapshot_id, file_id, source_ids))
    for pattern in patterns:
        for match in pattern.finditer(content):
            name = next(group for group in match.groups() if group)
            line = content.count("\n", 0, match.start()) + 1
            body = match.group(0)
            symbols.append(
                SymbolRevision(
                    stable_id("coding-symbol-v1", file_id, name, line),
                    snapshot_id,
                    file_id,
                    "definition",
                    name,
                    signature=body.split("\n", 1)[0][:240],
                    line_start=line,
                    line_end=line,
                    body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    source_ids=list(source_ids),
                )
            )
    return file_rev, symbols[:128]
