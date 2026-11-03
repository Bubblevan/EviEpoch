"""Stored-parent ancestry and symbol precision (TRD §19.2, §19.3 step 3)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.coding.ingest import ingest_sources
from eviepoch.adapters.coding.repo_import import import_authorized_repository
from eviepoch.adapters.coding.snapshot import ancestry_relation, dirty_overlay_hash, graph_from_payloads, snapshot_id
from eviepoch.adapters.coding.symbols import extract_symbols
from eviepoch.domain import Source, stable_id, timestamp
from experiments.coding.fixtures.fault_repo import build

REMOTE = "git@example.com:org/service.git"
RENAME_DIFF = (
    "diff --git a/pkg/handler.py b/pkg/handler.py\n"
    "--- a/pkg/handler.py\n"
    "+++ b/pkg/handler.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-def parse_name(value):\n"
    "+def parse_input(value):\n"
    "     return value.strip()\n"
)
CALL_FILE = (
    "def caller():\n"
    "    callee()\n"
    "    getattr(obj, \"missing\")()\n"
    "\n"
    "def callee():\n"
    "    return 1\n"
)


def _sha(text: str) -> str:
    return text * 40


def _source(content: str, ordinal: int = 0, locator: dict | None = None) -> Source:
    return Source(
        stable_id("ns", "ancestry", ordinal, content[:24]),
        "ns",
        "ancestry",
        "session",
        ordinal,
        "tool",
        content,
        timestamp(1700000000000),
        "2024-01-01T00:00:00Z",
        locator=locator or {},
    )


def _payloads(extension, kind: str) -> list[dict]:
    return [item.payload for item in extension.artifacts if item.kind == kind]


class AncestryTests(unittest.TestCase):
    def test_missing_parents_stay_unknown(self) -> None:
        left, right = _sha("a"), _sha("b")
        self.assertEqual(ancestry_relation(left, right, {}), "unknown")
        self.assertNotEqual(ancestry_relation(left, right, {}), "ancestor")
        self.assertEqual(ancestry_relation(left, left, {}), "same")
        self.assertEqual(ancestry_relation(None, right, {}), "unknown")

    def test_overlay_does_not_change_commit_relation(self) -> None:
        commit, tree = _sha("c"), _sha("d")
        first, _provisional = snapshot_id(
            "ns", "repo", commit_sha=commit, tree_sha=tree, overlay=dirty_overlay_hash("one\n"),
        )
        second, _provisional = snapshot_id(
            "ns", "repo", commit_sha=commit, tree_sha=tree, overlay=dirty_overlay_hash("two\n"),
        )
        self.assertNotEqual(first, second)
        self.assertEqual(ancestry_relation(commit, commit, {}), "same")

    def test_stored_parents_classify_fork_merge_and_rollback(self) -> None:
        c1, c2, c5, c6 = _sha("1"), _sha("2"), _sha("5"), _sha("6")
        merge, rollback = _sha("e"), _sha("f")
        tree = {"1": _sha("a"), "2": _sha("b"), "5": _sha("c"), "6": _sha("d"), "e": _sha("c"), "f": _sha("b")}
        lines = [
            f"repository: {REMOTE}",
            f"commit: {rollback}",
            f"tree: {tree['f']}",
            f"parents: {merge}",
            f"ancestry {c1} tree {tree['1']}",
            f"ancestry {c2} tree {tree['2']} {c1}",
            f"ancestry {c5} tree {tree['5']} {c2}",
            f"ancestry {c6} tree {tree['6']} {c2}",
            f"ancestry {merge} tree {tree['e']} {c2} {c5}",
            f"ancestry {rollback} tree {tree['f']} {merge}",
            f"reverts {rollback} {c5}",
        ]
        extension = ingest_sources([_source("\n".join(lines) + "\n", 1)])
        parents, reverts, trees = graph_from_payloads(_payloads(extension, "coding_snapshot"))
        self.assertEqual(ancestry_relation(c6, c5, parents, reverts, trees), "fork")
        self.assertEqual(ancestry_relation(c5, c6, parents, reverts, trees), "fork")
        self.assertEqual(ancestry_relation(merge, c5, parents, reverts, trees), "merge")
        self.assertEqual(ancestry_relation(merge, c2, parents, reverts, trees), "merge")
        self.assertEqual(ancestry_relation(rollback, c5, parents, reverts, trees), "rollback")
        self.assertEqual(ancestry_relation(rollback, c2, parents, reverts, trees), "rollback")
        self.assertEqual(ancestry_relation(c2, c5, parents, reverts, trees), "ancestor")
        self.assertEqual(ancestry_relation(c5, c2, parents, reverts, trees), "descendant")
        self.assertEqual(ancestry_relation(c5, _sha("9"), parents, reverts, trees), "unknown")
        self.assertTrue(any(edge.kind == "REVERTS" and edge.derivation == "source_stated" for edge in extension.edges))

    def test_fault_repo_fork_merge_rollback_overlay_and_distinct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = build(Path(directory))
            repo_a = built["repo_a"]
            repo_b = built["repo_b"]
            subjects = _subjects(repo_a)
            c5 = subjects["c5 rename parse_name to parse_input"]
            c6 = subjects["c6 fork adds notes only"]
            head = _git(repo_a, "rev-parse", "HEAD")
            handler = repo_a / "pkg" / "handler.py"
            handler.write_bytes(handler.read_bytes() + b"# dirty overlay\n")
            _left, extension = import_authorized_repository("ns", repo_a, "session", "import-a")
            _right, other = import_authorized_repository("ns", repo_b, "session", "import-b")

        snapshots = _payloads(extension, "coding_snapshot")
        parents, reverts, trees = graph_from_payloads(snapshots)
        merge = next(item["commit_sha"] for item in snapshots if len(item["parent_shas"]) >= 2)
        reverted = next(item for item in snapshots if item.get("reverts") == c5)
        self.assertEqual(ancestry_relation(c6, c5, parents, reverts, trees), "fork")
        self.assertIn(c5, next(item["parent_shas"] for item in snapshots if item["commit_sha"] == merge))
        self.assertEqual(ancestry_relation(merge, c5, parents, reverts, trees), "merge")
        self.assertEqual(ancestry_relation(reverted["commit_sha"], c5, parents, reverts, trees), "rollback")
        self.assertEqual(reverted["commit_sha"], head)
        dirty = [item for item in snapshots if item["commit_sha"] == head and item["dirty_overlay_hash"]]
        clean = [item for item in snapshots if item["commit_sha"] == head and not item["dirty_overlay_hash"]]
        self.assertEqual(len(dirty), 1)
        self.assertEqual(len(clean), 1)
        self.assertNotEqual(dirty[0]["snapshot_id"], clean[0]["snapshot_id"])
        self.assertEqual(ancestry_relation(head, head, parents, reverts, trees), "same")

        file_a = next(item for item in _payloads(extension, "coding_file_revision") if item["path"] == "pkg/handler.py")
        file_b = next(item for item in _payloads(other, "coding_file_revision") if item["path"] == "pkg/handler.py")
        self.assertEqual(file_a["path"], file_b["path"])
        self.assertNotEqual(file_a["file_id"], file_b["file_id"])
        self.assertNotEqual(file_a["content_hash"], file_b["content_hash"])
        self.assertEqual(len(file_a["content_hash"]), 40)
        symbol = next(
            item for item in _payloads(extension, "coding_symbol_revision")
            if item["file_id"] == file_a["file_id"] and item["qualified_name"] == "parse_name"
        )
        self.assertEqual(symbol["precision"], "tree_sitter")
        self.assertEqual(symbol["syntax_type"], "function_definition")
        self.assertTrue(symbol["signature"].startswith("def parse_name"))
        self.assertEqual(len(symbol["body_hash"]), 64)
        self.assertEqual(symbol["rename_status"], "not_rename")
        self.assertIsNotNone(symbol["line_start"])
        other_symbol = next(
            item for item in _payloads(other, "coding_symbol_revision")
            if item["file_id"] == file_b["file_id"] and item["qualified_name"] == "parse_name"
        )
        self.assertNotEqual(symbol["body_hash"], other_symbol["body_hash"])
        self.assertTrue(any(edge.kind == "REVERTS" for edge in extension.edges))
        self.assertFalse(any(edge.kind == "CALLS" and edge.derivation == "source_stated" for edge in extension.edges))


class SymbolTests(unittest.TestCase):
    def test_full_file_uses_tree_sitter_and_marks_static_calls(self) -> None:
        extension = ingest_sources([
            _source(
                f"repository: {REMOTE}\n",
                2,
                {"file_bodies": {"pkg/calls.py": CALL_FILE, "pkg/box.py": "class Box:\n    def open(self):\n        return 1\n"}},
            )
        ])
        calls = [edge for edge in extension.edges if edge.kind == "CALLS"]
        symbols = {item.payload["symbol_id"]: item.payload for item in extension.artifacts if item.kind == "coding_symbol_revision"}
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].derivation, "static_approximate")
        self.assertEqual(symbols[calls[0].origin]["qualified_name"], "caller")
        self.assertEqual(symbols[calls[0].target]["qualified_name"], "callee")
        self.assertNotIn("missing", {item["qualified_name"] for item in symbols.values()})
        box = next(item for item in symbols.values() if item["qualified_name"] == "Box.open")
        self.assertEqual(box["precision"], "tree_sitter")
        self.assertTrue(box["signature"].startswith("def open"))
        self.assertEqual(len(box["body_hash"]), 64)

    def test_non_python_full_file_is_marked_approximate(self) -> None:
        _file, symbols, edges = extract_symbols("pkg/helper.js", "function helper() {\n  return 1;\n}\n", "snap", ["s"])
        self.assertEqual(symbols[0].precision, "static_approximate")
        self.assertEqual(symbols[0].qualified_name, "helper")
        self.assertEqual(edges, [])

    def test_diff_fragment_is_approximate_and_rename_stays_unverified(self) -> None:
        content = f"repository: {REMOTE}\nrename parse_name to parse_input\n{RENAME_DIFF}"
        extension = ingest_sources([_source(content, 3)])
        file_rev = next(item.payload for item in extension.artifacts if item.kind == "coding_file_revision")
        self.assertIsNone(file_rev["content_hash"])
        added = next(item.payload for item in extension.artifacts if item.kind == "coding_symbol_revision" and item.payload["qualified_name"] == "parse_input")
        self.assertEqual(added["precision"], "static_approximate")
        self.assertEqual(added["rename_status"], "unverified")
        self.assertEqual(added["rename_of"], "parse_name")
        self.assertIsNone(added["line_start"])
        self.assertFalse(any(edge.kind == "CALLS" and edge.derivation == "source_stated" for edge in extension.edges))

    def test_explicit_rename_evidence_is_verified(self) -> None:
        content = f"repository: {REMOTE}\nrename verified: parse_name -> parse_input\n{RENAME_DIFF}"
        extension = ingest_sources([_source(content, 4)])
        added = next(item.payload for item in extension.artifacts if item.kind == "coding_symbol_revision" and item.payload["qualified_name"] == "parse_input")
        self.assertEqual(added["rename_status"], "verified")
        self.assertEqual(added["rename_of"], "parse_name")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _subjects(repo: Path) -> dict[str, str]:
    found = {}
    for line in _git(repo, "log", "--all", "--format=%H%x09%s").splitlines():
        sha, subject = line.split("\t", 1)
        found[subject] = sha
    return found


if __name__ == "__main__":
    unittest.main()
