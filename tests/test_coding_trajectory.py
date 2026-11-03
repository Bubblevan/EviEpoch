from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.coding.ingest import ingest_sources
from eviepoch.adapters.coding.repo_import import UnauthorizedRepository, import_authorized_repository
from eviepoch.config import Settings
from eviepoch.domain import Source, stable_id, timestamp
from eviepoch.models import AddRequest, Message
from eviepoch.pipeline import MemoryPipeline
from tests import test_lifecycle as fixtures

REMOTE = "git@example.com:org/service.git"
COMMIT = "abcdef0123456789"
TREE = "1234abcd1234abcd"
DIFF = (
    "diff --git a/pkg/handler.py b/pkg/handler.py\n"
    "--- a/pkg/handler.py\n"
    "+++ b/pkg/handler.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-raise NullPointerError\n"
    "+return safe_default()\n"
)


def _source(content: str, ordinal: int = 0) -> Source:
    return Source(
        stable_id("ns", "trajectory", ordinal, content[:24]),
        "ns",
        "trajectory",
        "session",
        ordinal,
        "tool",
        content,
        timestamp(1700000000000),
        "2024-01-01T00:00:00Z",
    )


def _aml() -> str:
    return (
        f"repository: {REMOTE}\n"
        f"commit: {COMMIT}\n"
        f"tree: {TREE}\n"
        f"{DIFF}"
        "pytest pkg/tests/test_handler.py\n"
        "FAILED pkg/tests/test_handler.py::test_null - NullPointerError\n"
        "exit code: 1\n"
    )


def _native(client: str) -> str:
    return json.dumps(
        {
            "tool": "Bash",
            "client": client,
            "tool_call_id": "call-1",
            "repository": REMOTE,
            "commit": COMMIT,
            "tree": TREE,
            "command": "pytest pkg/tests/test_handler.py",
            "diff": DIFF,
            "output": "FAILED pkg/tests/test_handler.py::test_null - NullPointerError\nexit code: 1\n",
            "exit_code": 1,
        },
        ensure_ascii=False,
    )


def _kind(extension, kind: str):
    return next(item for item in extension.artifacts if item.kind == kind)


def _git(repo: Path, *args: str, when: str = "2026-01-01T00:00:00+0000") -> str:
    completed = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=EviEpoch Fixture", "-c", "user.email=fixture@eviepoch.local", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
        env={
            **dict(**{key: value for key, value in __import__("os").environ.items()}),
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
            "GIT_AUTHOR_NAME": "EviEpoch Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@eviepoch.local",
            "GIT_COMMITTER_NAME": "EviEpoch Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@eviepoch.local",
        },
    )
    return completed.stdout.strip()


class CodingTrajectoryTests(unittest.TestCase):
    def test_string_and_native_share_snapshot_patch_and_test(self) -> None:
        aml = ingest_sources([_source(_aml(), 1)])
        native = ingest_sources([_source(_native("claude-code"), 2)])
        other_client = ingest_sources([_source(_native("codex"), 3)])

        aml_snap = _kind(aml, "coding_snapshot").payload
        native_snap = _kind(native, "coding_snapshot").payload
        self.assertEqual(aml_snap["repo_id"], native_snap["repo_id"])
        self.assertEqual(aml_snap["snapshot_id"], native_snap["snapshot_id"])
        self.assertEqual(aml_snap["commit_sha"], COMMIT)
        self.assertFalse(aml_snap["provisional"])
        self.assertIsNone(aml_snap["dirty_overlay_hash"])

        aml_patch = _kind(aml, "coding_patch_attempt").payload
        native_patch = _kind(native, "coding_patch_attempt").payload
        self.assertEqual(aml_patch["raw_diff"], DIFF)
        self.assertEqual(aml_patch["patch_hash"], native_patch["patch_hash"])
        source = _source(_aml(), 1)
        span = next(item for item in aml.spans if item.quote == DIFF)
        self.assertEqual(source.content.encode()[span.byte_start:span.byte_end].decode(), DIFF)
        self.assertNotIn("pytest", aml_patch["raw_diff"])

        self.assertEqual(_kind(aml, "coding_test_run").payload["exit_code"], 1)
        self.assertEqual(_kind(native, "coding_test_run").payload["exit_code"], 1)
        native_source = _source(_native("claude-code"), 2)
        self.assertEqual(
            _kind(native, "coding_engineering_event").payload["event_id"],
            stable_id("coding-event-v1", "ns", "claude-code", "call-1", native_source.id),
        )
        self.assertNotEqual(
            _kind(native, "coding_engineering_event").payload["event_id"],
            _kind(other_client, "coding_engineering_event").payload["event_id"],
        )
        self.assertEqual(_kind(native, "coding_engineering_event").payload["client"], "claude-code")
        self.assertEqual(_kind(other_client, "coding_engineering_event").payload["client"], "codex")
        self.assertEqual(_kind(native, "coding_engineering_event").payload["tool_call_id"], "call-1")

    def test_missing_sha_exit_and_file_stay_unknown(self) -> None:
        content = (
            "repository: git@example.com:org/service.git\n"
            "files modified: pkg/handler.py\n"
            "FAILED pkg/handler.py::test_null\n"
        )
        extension = ingest_sources([_source(content, 4)])
        snapshot = _kind(extension, "coding_snapshot").payload
        self.assertTrue(snapshot["provisional"])
        self.assertIsNone(snapshot["commit_sha"])
        test_run = _kind(extension, "coding_test_run").payload
        self.assertIsNone(test_run["exit_code"])
        self.assertEqual(test_run["outcome"], "failure")
        file_rev = next(item for item in extension.artifacts if item.kind == "coding_file_revision")
        self.assertIsNone(file_rev.payload["content_hash"])
        plain = ingest_sources([_source("repository: git@example.com:org/service.git\ncommit: abcdef0123456789\n", 7)])
        self.assertFalse(any(item.kind == "coding_test_run" for item in plain.artifacts))

    def test_uncommitted_diffs_split_one_commit_into_two_snapshots(self) -> None:
        def message(line: str) -> str:
            return (
                f"repository: {REMOTE}\ncommit: {COMMIT}\nuncommitted:\n"
                "diff --git a/pkg/handler.py b/pkg/handler.py\n"
                "--- a/pkg/handler.py\n+++ b/pkg/handler.py\n@@ -1 +1 @@\n"
                f"-a\n+{line}\n"
            )

        first = _kind(ingest_sources([_source(message("b"), 5)]), "coding_snapshot").payload
        second = _kind(ingest_sources([_source(message("c"), 6)]), "coding_snapshot").payload
        self.assertEqual(first["commit_sha"], second["commit_sha"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertFalse(first["provisional"])
        self.assertTrue(first["dirty_overlay_hash"])
        self.assertNotEqual(first["dirty_overlay_hash"], second["dirty_overlay_hash"])

    def test_authorized_import_uses_only_its_own_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent"
            child = root / "child"
            nested = parent / "nested"
            for path in (parent, child):
                path.mkdir()
                _git(path, "init", "-b", "main")
                (path / "README").write_bytes(b"hello\n")
                _git(path, "add", "-A")
                _git(path, "commit", "-m", path.name)
            nested.mkdir()
            with self.assertRaises(UnauthorizedRepository):
                import_authorized_repository("ns", nested, "session", "import-nested")
            (child / "README").write_bytes(b"hello\ndirty\n")
            _source_obj, extension = import_authorized_repository("ns", child, "session", "import-child")
            snapshot = _kind(extension, "coding_snapshot").payload
            self.assertEqual(snapshot["commit_sha"], _git(child, "rev-parse", "HEAD"))
            self.assertNotEqual(snapshot["commit_sha"], _git(parent, "rev-parse", "HEAD"))
            self.assertFalse(snapshot["provisional"])
            self.assertTrue(snapshot["dirty_overlay_hash"])
            file_rev = next(item for item in extension.artifacts if item.kind == "coding_file_revision")
            self.assertEqual(file_rev.payload["path"], "README")
            self.assertEqual(len(file_rev.payload["content_hash"]), 40)
            self.assertEqual(_kind(extension, "coding_engineering_event").payload["tool_name"], "repo-import")

    def test_pipeline_reads_original_diff_bytes_back(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            content = _aml()
            provider = fixtures.AnnotatedProviders()
            provider.register(content, nodes=())
            config = Settings(
                task_adapter="coding",
                db_path=str(Path(directory) / "coding.sqlite3"),
                llm_api_key="test",
                embed_api_key="test",
                embed_base_url="https://example.test/v1",
                embed_dimensions=32,
                auth_scheme="none",
                rerank_enabled=False,
                window_radius=0,
            )
            pipeline = MemoryPipeline(config, provider)
            pipeline.start()
            try:
                pipeline.add(AddRequest("trajectory-add", "dev-a", "sess", [Message("tool", content, 1700000000000)]))
                connection = sqlite3.connect(config.db_path)
                try:
                    row = connection.execute("SELECT payload FROM ee_patch_attempts").fetchone()
                finally:
                    connection.close()
                self.assertIsNotNone(row)
                self.assertEqual(json.loads(row[0])["raw_diff"], DIFF)
            finally:
                pipeline.close()


if __name__ == "__main__":
    unittest.main()
