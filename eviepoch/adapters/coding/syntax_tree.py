"""tree-sitter syntax tree for a complete Python file.

Named nodes keep their type, field, and byte span. The source text stays in the
file body; a span slices back to the same bytes. A diff fragment is not a tree.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .symbols import language_for_path

_NODE_CAP = 512


def parse_syntax_tree(path: str, content: str) -> dict[str, Any] | None:
    """Return a syntax tree, or None when this file has no tree-sitter grammar."""
    if language_for_path(path) != "python" or not content:
        return None
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython
    except ImportError:
        return None
    data = content.encode("utf-8")
    try:
        parser = Parser(Language(tspython.language()))
        root = parser.parse(data).root_node
    except (TypeError, ValueError, OSError):
        return None
    count = 0

    def walk(node, field: str | None) -> dict[str, Any] | None:
        nonlocal count
        if not node.is_named or count >= _NODE_CAP:
            return None
        count += 1
        item: dict[str, Any] = {
            "type": node.type,
            "start_byte": node.start_byte,
            "end_byte": node.end_byte,
        }
        if field:
            item["field"] = field
        children: list[dict[str, Any]] = []
        for index, child in enumerate(node.children):
            walked = walk(child, node.field_name_for_child(index))
            if walked is not None:
                children.append(walked)
        if children:
            item["children"] = children
        return item

    tree = walk(root, None)
    if tree is None:
        return None
    return {
        "language": "python",
        "precision": "tree_sitter",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "has_error": bool(root.has_error),
        "truncated": count >= _NODE_CAP,
        "node_count": count,
        "tree": tree,
    }


def source_slice(content: str, node: dict[str, Any]) -> str:
    """Slice the original source with the node's stored byte span."""
    data = content.encode("utf-8")
    start = int(node["start_byte"])
    end = int(node["end_byte"])
    return data[start:end].decode("utf-8")
