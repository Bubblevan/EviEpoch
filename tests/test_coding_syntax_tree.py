"""Syntax trees for complete Python files."""
from __future__ import annotations

import unittest

from eviepoch.adapters.coding.ingest import ingest_sources
from eviepoch.adapters.coding.syntax_tree import parse_syntax_tree, source_slice
from eviepoch.domain import Source, stable_id, timestamp

HANDLER = "def parse_name(value):\n    return value.strip()\n"
REMOTE = "git@example.com:org/service.git"


def _source(content: str, bodies: dict[str, str] | None = None) -> Source:
    source = Source(
        stable_id("ns", "syntax", 0, "source-v1"),
        "ns",
        "syntax",
        "session",
        0,
        "tool",
        content,
        timestamp(1700000000000),
        "2024-01-01T00:00:00Z",
    )
    if bodies is not None:
        source.locator = {"file_bodies": bodies}
    return source


def _find(node: dict, node_type: str, field: str | None = None) -> dict:
    if node.get("type") == node_type and (field is None or node.get("field") == field):
        return node
    for child in node.get("children") or []:
        found = _find(child, node_type, field)
        if found:
            return found
    raise AssertionError(f"{node_type} not in tree")


class SyntaxTreeTests(unittest.TestCase):
    def test_named_spans_slice_back_to_the_source(self) -> None:
        parsed = parse_syntax_tree("pkg/handler.py", HANDLER)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["precision"], "tree_sitter")
        self.assertFalse(parsed["has_error"])
        self.assertFalse(parsed["truncated"])
        tree = parsed["tree"]
        self.assertEqual(tree["type"], "module")
        self.assertEqual(source_slice(HANDLER, tree), HANDLER)
        function = _find(tree, "function_definition")
        self.assertEqual(source_slice(HANDLER, function), "def parse_name(value):\n    return value.strip()")
        self.assertIn(source_slice(HANDLER, function), HANDLER)
        name = _find(function, "identifier", "name")
        self.assertEqual(source_slice(HANDLER, name), "parse_name")
        self.assertEqual(HANDLER.encode()[name["start_byte"]:name["end_byte"]].decode(), "parse_name")

    def test_non_python_and_empty_input_have_no_tree(self) -> None:
        self.assertIsNone(parse_syntax_tree("pkg/helper.js", "function helper() { return 1; }\n"))
        self.assertIsNone(parse_syntax_tree("pkg/handler.py", ""))

    def test_broken_python_keeps_the_error_flag(self) -> None:
        parsed = parse_syntax_tree("pkg/handler.py", "def parse_name(\n")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertTrue(parsed["has_error"])
        self.assertEqual(parsed["tree"]["type"], "module")

    def test_full_file_is_stored_and_a_diff_fragment_is_not(self) -> None:
        full = ingest_sources([
            _source(
                f"repository: {REMOTE}\ncommit: {'a' * 40}\n",
                {"pkg/handler.py": HANDLER},
            )
        ])
        trees = [item.payload for item in full.artifacts if item.kind == "coding_syntax_tree"]
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0]["path"], "pkg/handler.py")
        self.assertEqual(source_slice(HANDLER, _find(trees[0]["tree"], "identifier", "name")), "parse_name")
        fragment = ingest_sources([
            _source(
                f"repository: {REMOTE}\n"
                "diff --git a/pkg/handler.py b/pkg/handler.py\n"
                "--- a/pkg/handler.py\n"
                "+++ b/pkg/handler.py\n"
                "@@ -1 +1 @@\n"
                "-def parse_name(value):\n"
                "+def parse_input(value):\n"
            )
        ])
        self.assertFalse(any(item.kind == "coding_syntax_tree" for item in fragment.artifacts))


if __name__ == "__main__":
    unittest.main()
