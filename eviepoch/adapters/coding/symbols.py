"""Symbol, import, and call indexing for full files and observed fragments."""
from __future__ import annotations

import hashlib
import re

from ...domain import Edge, stable_id
from .models import FileRevision, SymbolRevision

_PY = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)", re.M)
_JS = re.compile(r"(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()", re.M)
_GO = re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)", re.M)
_DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)")
_IMPORT = re.compile(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))")
_CALL = re.compile(r"(?<![\w.])([A-Za-z_][\w]*)\s*\(")
_VERIFIED_RENAME = re.compile(r"(?m)^rename verified:\s*([A-Za-z_][\w.]*)\s*->\s*([A-Za-z_][\w.]*)\s*$")
_DYNAMIC_NAMES = {"getattr", "eval", "exec", "__import__"}
_SKIP_CALLS = _DYNAMIC_NAMES | {"def", "class", "if", "for", "while", "with", "return", "elif"}


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


def diff_hunks(*diffs: str | None) -> dict[str, str]:
    """Map each touched path to the diff section that shows it."""
    found: dict[str, str] = {}
    for diff in diffs:
        if not diff:
            continue
        for part in re.split(r"(?=^diff --git )", diff, flags=re.M):
            match = re.search(r"^diff --git a/(.+?) b/(\S+)", part, re.M)
            if not match:
                continue
            path = match.group(2).strip()
            if path and path != "/dev/null":
                found[path] = part
    return found


def _symbol(
    file_id: str,
    snapshot_id: str,
    source_ids: list[str],
    syntax_type: str,
    qualified_name: str,
    signature: str,
    *,
    line_start: int | None,
    line_end: int | None,
    body: str,
    precision: str,
    rename_status: str = "not_rename",
    rename_of: str | None = None,
    identity: str = "",
) -> SymbolRevision:
    return SymbolRevision(
        stable_id("coding-symbol-v1", file_id, qualified_name, identity or line_start or syntax_type),
        snapshot_id,
        file_id,
        syntax_type,
        qualified_name,
        signature=signature[:240],
        line_start=line_start,
        line_end=line_end,
        body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
        source_ids=list(source_ids),
        precision=precision,
        rename_status=rename_status,
        rename_of=rename_of,
    )


def _tree_sitter_symbols(
    path: str, content: str, snapshot_id: str, file_id: str, source_ids: list[str]
) -> list[SymbolRevision]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython
    except ImportError:
        return []
    if language_for_path(path) != "python":
        return []
    data = content.encode("utf-8")
    try:
        parser = Parser(Language(tspython.language()))
        root = parser.parse(data).root_node
    except (TypeError, ValueError, OSError):
        return []
    symbols: list[SymbolRevision] = []

    def text_of(node) -> str:
        return data[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def line_of(offset: int) -> int:
        return data[:offset].count(b"\n") + 1

    def walk(node, class_prefix: str) -> None:
        if len(symbols) >= 128:
            return
        if node.type in {"function_definition", "class_definition"}:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text_of(name_node)
                qualified = f"{class_prefix}.{name}" if class_prefix else name
                body = text_of(node)
                symbols.append(
                    _symbol(
                        file_id,
                        snapshot_id,
                        source_ids,
                        node.type,
                        qualified,
                        body.split("\n", 1)[0],
                        line_start=line_of(node.start_byte),
                        line_end=line_of(node.end_byte),
                        body=body,
                        precision="tree_sitter",
                        identity=str(line_of(node.start_byte)),
                    )
                )
                next_prefix = qualified if node.type == "class_definition" else class_prefix
                for child in node.children:
                    walk(child, next_prefix)
            return
        if node.type in {"import_statement", "import_from_statement"}:
            module = node.child_by_field_name("module_name")
            if module is None:
                module = next((child for child in node.children if child.type == "dotted_name"), None)
            name = text_of(module) if module is not None else text_of(node).split("\n", 1)[0]
            body = text_of(node).split("\n", 1)[0]
            symbols.append(
                _symbol(
                    file_id,
                    snapshot_id,
                    source_ids,
                    "import",
                    name,
                    body,
                    line_start=line_of(node.start_byte),
                    line_end=line_of(node.end_byte),
                    body=body,
                    precision="tree_sitter",
                    identity=f"import:{line_of(node.start_byte)}",
                )
            )
            return
        for child in node.children:
            walk(child, class_prefix)

    walk(root, "")
    return symbols


def _regex_symbols(
    path: str, content: str, snapshot_id: str, file_id: str, source_ids: list[str]
) -> list[SymbolRevision]:
    lang = language_for_path(path)
    patterns = []
    if lang == "python":
        patterns.append(_PY)
    elif lang == "javascript":
        patterns.append(_JS)
    elif lang == "go":
        patterns.append(_GO)
    symbols: list[SymbolRevision] = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            name = next(group for group in match.groups() if group)
            line = content.count("\n", 0, match.start()) + 1
            body = match.group(0)
            symbols.append(
                _symbol(
                    file_id,
                    snapshot_id,
                    source_ids,
                    "definition",
                    name,
                    body.split("\n", 1)[0],
                    line_start=line,
                    line_end=line,
                    body=body,
                    precision="static_approximate",
                    identity=str(line),
                )
            )
    for match in _IMPORT.finditer(content):
        name = next(group for group in match.groups() if group)
        line = content.count("\n", 0, match.start()) + 1
        symbols.append(
            _symbol(
                file_id,
                snapshot_id,
                source_ids,
                "import",
                name,
                match.group(0),
                line_start=line,
                line_end=line,
                body=match.group(0),
                precision="static_approximate",
                identity=f"import:{line}",
            )
        )
    return symbols[:128]


def _fragment_line(line: str) -> tuple[str, str] | None:
    if line.startswith(("+++", "---", "@@", "diff ", "index ", "\\")):
        return None
    kind = "context"
    body = line
    if line.startswith("+"):
        kind = "add"
        body = line[1:]
    elif line.startswith("-"):
        kind = "del"
        body = line[1:]
    elif line.startswith(" "):
        body = line[1:]
    return kind, body


def _fragment_symbols(
    content: str, snapshot_id: str, file_id: str, source_ids: list[str]
) -> list[SymbolRevision]:
    added: list[tuple[str, str]] = []
    removed: list[str] = []
    symbols: list[SymbolRevision] = []
    for line in content.splitlines():
        parsed = _fragment_line(line)
        if parsed is None:
            continue
        kind, body = parsed
        definition = _DEF.match(body.strip())
        if definition:
            name = next(group for group in definition.groups() if group)
            if kind == "del":
                removed.append(name)
                symbols.append(
                    _symbol(
                        file_id,
                        snapshot_id,
                        source_ids,
                        "removed_fragment",
                        name,
                        body.strip(),
                        line_start=None,
                        line_end=None,
                        body=body.strip(),
                        precision="static_approximate",
                        identity=f"removed:{len(removed)}",
                    )
                )
            else:
                added.append((name, body.strip()))
            continue
        imported = _IMPORT.match(body.strip())
        if imported and kind != "del":
            name = next(group for group in imported.groups() if group)
            symbols.append(
                _symbol(
                    file_id,
                    snapshot_id,
                    source_ids,
                    "import",
                    name,
                    body.strip(),
                    line_start=None,
                    line_end=None,
                    body=body.strip(),
                    precision="static_approximate",
                    identity=f"import:{name}",
                )
            )
    rename_of = None
    added_names = list(dict.fromkeys(name for name, _body in added))
    removed_names = list(dict.fromkeys(removed))
    if len(removed_names) == 1 and len(added_names) == 1 and removed_names[0] != added_names[0]:
        rename_of = removed_names[0]
    for index, (name, signature) in enumerate(added, start=1):
        symbols.append(
            _symbol(
                file_id,
                snapshot_id,
                source_ids,
                "definition",
                name,
                signature,
                line_start=None,
                line_end=None,
                body=signature,
                precision="static_approximate",
                rename_status="unverified" if rename_of else "not_rename",
                rename_of=rename_of,
                identity=f"fragment:{index}",
            )
        )
    return symbols[:128]


def _apply_verified_renames(
    symbols: list[SymbolRevision],
    evidence: str,
    snapshot_id: str,
    file_id: str,
    source_ids: list[str],
) -> None:
    for match in _VERIFIED_RENAME.finditer(evidence or ""):
        old, new = match.group(1), match.group(2)
        matched = False
        for symbol in symbols:
            if symbol.qualified_name == new and symbol.syntax_type != "removed_fragment":
                symbol.rename_of = old
                symbol.rename_status = "verified"
                matched = True
        if matched:
            continue
        symbols.append(
            _symbol(
                file_id,
                snapshot_id,
                source_ids,
                "definition",
                new,
                new,
                line_start=None,
                line_end=None,
                body=new,
                precision="static_approximate",
                rename_status="verified",
                rename_of=old,
                identity=f"verified:{old}",
            )
        )


def _enclosing(symbols: list[SymbolRevision], line: int) -> SymbolRevision | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.syntax_type not in {"import", "removed_fragment"}
        and symbol.line_start is not None
        and symbol.line_end is not None
        and symbol.line_start <= line <= symbol.line_end
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda symbol: (symbol.line_end or line) - (symbol.line_start or line))


def _call_edges(content: str, symbols: list[SymbolRevision], source_ids: list[str]) -> list[Edge]:
    """Static bare calls become approximate edges. Dynamic calls are not edges."""
    counts: dict[str, int] = {}
    for symbol in symbols:
        if symbol.syntax_type == "removed_fragment":
            continue
        short = symbol.qualified_name.split(".")[-1]
        counts[short] = counts.get(short, 0) + 1
    defined = {
        symbol.qualified_name.split(".")[-1]: symbol
        for symbol in symbols
        if symbol.syntax_type != "removed_fragment" and counts.get(symbol.qualified_name.split(".")[-1]) == 1
    }
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for match in _CALL.finditer(content):
        name = match.group(1)
        if name in _SKIP_CALLS:
            continue
        prefix = content[max(0, match.start() - 12):match.start()]
        if re.search(r"(?:def|class|import|from|getattr|eval|exec)\s*$", prefix):
            continue
        target = defined.get(name)
        if target is None:
            continue
        line = content.count("\n", 0, match.start()) + 1
        caller = _enclosing(symbols, line)
        if caller is None or caller.symbol_id == target.symbol_id:
            continue
        key = (caller.symbol_id, target.symbol_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            Edge(
                stable_id("coding-edge-v1", caller.symbol_id, "CALLS", target.symbol_id),
                caller.symbol_id,
                target.symbol_id,
                "CALLS",
                list(source_ids),
                derivation="static_approximate",
            )
        )
    return edges


def extract_symbols(
    path: str,
    content: str,
    snapshot_id: str,
    source_ids: list[str],
    *,
    completeness: str = "full",
    content_hash: str | None = None,
    evidence: str = "",
) -> tuple[FileRevision, list[SymbolRevision], list[Edge]]:
    """Index a full file, or only the fragments a diff actually shows.

    A fragment does not receive a file content hash. Regex fallback and diff
    fragments are static approximations. A rename stays unverified until the
    source states `rename verified: old -> new`.
    """
    file_id = stable_id("coding-file-v1", snapshot_id, path)
    if completeness == "fragment":
        stored_hash = content_hash
    elif content_hash:
        stored_hash = content_hash
    elif content:
        stored_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    else:
        stored_hash = None
    file_rev = FileRevision(file_id, snapshot_id, path, language_for_path(path), stored_hash, list(source_ids))
    if completeness == "fragment":
        symbols = _fragment_symbols(content, snapshot_id, file_id, source_ids)
        calls: list[Edge] = []
    else:
        symbols = _tree_sitter_symbols(path, content, snapshot_id, file_id, source_ids) if content else []
        if not symbols and content:
            symbols = _regex_symbols(path, content, snapshot_id, file_id, source_ids)
        calls = _call_edges(content, symbols, source_ids) if content else []
    depends = [
        Edge(
            stable_id("coding-edge-v1", file_id, "DEPENDS_ON", symbol.symbol_id),
            file_id,
            symbol.symbol_id,
            "DEPENDS_ON",
            list(source_ids),
            "static_approximate",
        )
        for symbol in symbols
        if symbol.syntax_type == "import"
    ]
    _apply_verified_renames(symbols, evidence, snapshot_id, file_id, source_ids)
    return file_rev, symbols[:128], calls + depends
