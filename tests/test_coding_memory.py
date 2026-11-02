from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eviepoch.adapters.coding.ingest import ingest_sources
from eviepoch.adapters.coding.native_events import normalize_native_source
from eviepoch.adapters.coding.parse_agent_xml import parse_agent_xml, parse_observations
from eviepoch.adapters.coding.parse import parse_source
from eviepoch.adapters.coding.snapshot import snapshot_id
from eviepoch.config import Settings
from eviepoch.domain import Source, stable_id, timestamp
from eviepoch.models import AddRequest, Message, SearchRequest
from tests import test_lifecycle as fixtures
from eviepoch.pipeline import MemoryPipeline


class CodingMemoryTests(unittest.TestCase):
    def test_claude_mem_v15_parser_preserves_invalid_type(self) -> None:
        text = """
        <observation>
          <type>not-a-real-type</type>
          <title>Custom observer type</title>
          <facts><fact>detail</fact></facts>
        </observation>
        """
        parsed = parse_agent_xml(text)
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.observations[0].type, "not-a-real-type")

    def test_claude_mem_xml_parser_matches_upstream_shape(self) -> None:
        text = """
        <observation>
          <type>bugfix</type>
          <title>Fix OAuth callback</title>
          <facts><fact>Callback URL must match redirect_uri exactly</fact></facts>
          <files_modified><file>src/auth/oauth.py</file></files_modified>
        </observation>
        """
        parsed = parse_observations(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].type, "bugfix")
        self.assertEqual(parsed[0].title, "Fix OAuth callback")
        self.assertEqual(parsed[0].files_modified, ["src/auth/oauth.py"])

    def test_native_json_event_enriches_locator(self) -> None:
        source = Source(
            stable_id("ns", "req", 0, "source-v1"),
            "ns",
            "req",
            "session",
            0,
            "tool",
            '{"tool":"Bash","cwd":"/repo","commit":"abc1234","repository":"git@x/y.git","output":"ok"}',
            timestamp(1700000000000),
            "2024-01-01T00:00:00Z",
        )
        event = normalize_native_source(source)
        self.assertEqual(event.tool_name, "Bash")
        self.assertEqual(event.commit_sha, "abc1234")
        self.assertEqual(source.locator.get("tool_name"), "Bash")

    def test_snapshot_is_provisional_without_sha(self) -> None:
        sid, provisional = snapshot_id("ns", "repo-a")
        self.assertTrue(provisional)
        self.assertEqual(len(sid), 32)

    def test_parse_diff_and_test_run(self) -> None:
        content = (
            "repository: git@example.com:org/service.git\n"
            "commit: abcdef0123456789\n"
            "diff --git a/pkg/handler.py b/pkg/handler.py\n"
            "--- a/pkg/handler.py\n"
            "+++ b/pkg/handler.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-raise NullPointerError\n"
            "+return safe_default()\n"
            "pytest pkg/tests/test_handler.py\n"
            "FAILED pkg/tests/test_handler.py::test_null - NullPointerError\n"
            "exit code: 1\n"
        )
        source = Source(
            stable_id("ns", "req", 0, "source-v1"),
            "ns",
            "req",
            "session",
            0,
            "tool",
            content,
            timestamp(1700000000000),
            "2024-01-01T00:00:00Z",
        )
        parsed = parse_source(source)
        self.assertEqual(parsed.commit_sha, "abcdef0123456789")
        self.assertIsNotNone(parsed.diff_text)
        self.assertEqual(parsed.exit_code, 1)
        extension = ingest_sources([source])
        kinds = {artifact.kind for artifact in extension.artifacts}
        self.assertIn("coding_patch_attempt", kinds)
        self.assertIn("coding_test_run", kinds)

    def test_coding_add_and_search_round_trip(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        provider = fixtures.AnnotatedProviders()
        config = Settings(
            task_adapter="coding",
            db_path=str(Path(temp.name) / "coding.sqlite3"),
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
            diff = (
                "commit: deadbeefdeadbeef\n"
                "type: bugfix\n"
                "title: Fix NullPointer in handler\n"
                "diff --git a/pkg/handler.py b/pkg/handler.py\n"
                "--- a/pkg/handler.py\n"
                "+++ b/pkg/handler.py\n"
                "-raise NullPointerError\n"
                "+return safe_default()\n"
            )
            provider.register(diff, nodes=())
            pipeline.add(
                AddRequest(
                    request_id="coding-add-1",
                    user_id="dev-a",
                    session_id="sess-1",
                    messages=[Message("tool", diff, 1700000000000)],
                )
            )
            provider.plan_value = {
                "intents": ["fact"],
                "obligations": [{"query": "NullPointer handler patch pkg/handler.py"}],
            }
            response = pipeline.search(
                SearchRequest(user_id="dev-a", query="NullPointer handler patch pkg/handler.py", top_k=5)
            )
            self.assertTrue(response["data"])
            joined = "\n".join(item["content"] for item in response["data"])
            self.assertIn("NullPointer", joined)
        finally:
            pipeline.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
