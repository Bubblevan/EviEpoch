import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eviepoch.observability.diagnostics import (
    begin_request_trace,
    end_request_trace,
    log_dataflow_event,
    request_context,
)


class DataflowAuditTests(unittest.TestCase):
    def test_events_share_request_trace_and_keep_model_payload(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            env = {
                "EPOCH_PIPELINE_TRACE_ENABLED": "1",
                "EPOCH_PIPELINE_TRACE_DIR": str(Path(folder) / "audit"),
            }
            with patch.dict(os.environ, env):
                trace_id, token = begin_request_trace()
                try:
                    with request_context(operation="add", request_id="private-request-id",
                                         message_count=1, fingerprint_key="test-token"):
                        log_dataflow_event("llm_request", model="test-model",
                                           messages=[{"role": "user", "content": "private memory"}])
                finally:
                    end_request_trace(token)
            path = next((Path(folder) / "audit").glob("pipeline-traces-*.jsonl"))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["trace_id"], trace_id)
            self.assertEqual(record["operation"], "add")
            self.assertEqual(record["stage"], "llm_request")
            self.assertEqual(record["messages"][0]["content"], "private memory")
            self.assertNotIn("test-token", path.read_text(encoding="utf-8"))
            self.assertNotIn("private-request-id", path.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_oversized_event_is_replaced_with_bounded_fingerprint(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            env = {
                "EPOCH_PIPELINE_TRACE_ENABLED": "1",
                "EPOCH_PIPELINE_TRACE_DIR": str(Path(folder) / "audit"),
                "EPOCH_PIPELINE_TRACE_MAX_EVENT_BYTES": "128",
            }
            with patch.dict(os.environ, env):
                log_dataflow_event("large", content="x" * 1024)
            path = next((Path(folder) / "audit").glob("pipeline-traces-*.jsonl"))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(record["payload_omitted"])
            self.assertEqual(record["stage"], "large")
            self.assertLessEqual(path.stat().st_size, 256)


if __name__ == "__main__":
    unittest.main()
