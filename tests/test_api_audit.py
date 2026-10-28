import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from eviepoch.api import create_app
from eviepoch.config import Settings


class FakeMemory:
    def start(self):
        pass

    def close(self):
        pass

    def add(self, request):
        return {"success": True, "request_id": request.request_id,
                "user_id": request.user_id, "session_id": request.session_id}

    def search(self, request):
        if request.query == "large response":
            return {"data": [{"content": "y" * 1024}]}
        return {"data": []}


class PayloadAuditTests(unittest.TestCase):
    def _app(self, root: Path):
        config = Settings(db_path=str(root / "unused.sqlite3"), llm_api_key="test",
                          embed_api_key="test", embed_base_url="https://example.test/v1",
                          embed_dimensions=32, auth_token="local-test-token")
        return create_app(config, FakeMemory())

    def test_captures_add_search_bodies_but_not_auth_headers(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            env = {"EPOCH_AUDIT_DIR": str(root / "audit"),
                   "EPOCH_AUDIT_MAX_BODY_BYTES": "4096",
                   "EPOCH_AUDIT_MAX_DISK_BYTES": "1048576"}
            with patch.dict(os.environ, env):
                with TestClient(self._app(root)) as client:
                    headers = {"Authorization": "Bearer local-test-token"}
                    add = {"request_id": "audit-r1", "user_id": "u1", "session_id": "s1",
                           "messages": [{"role": "user", "content": "My address is Kyoto."}]}
                    self.assertEqual(client.post("/add", json=add, headers=headers).status_code, 200)
                    search = {"query": "Where do I live?", "user_id": "u1", "top_k": 5}
                    self.assertEqual(client.post("/search", json=search, headers=headers).status_code, 200)
            files = list((root / "audit").glob("api-requests-*.jsonl"))
            self.assertEqual(len(files), 1)
            records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["path"] for record in records], ["/add", "/search"])
            self.assertEqual([record["status"] for record in records], [200, 200])
            self.assertIn("My address is Kyoto", records[0]["request_body"])
            self.assertIn("Where do I live?", records[1]["request_body"])
            self.assertIn('"data":[]', records[1]["response_body"])
            self.assertNotIn("local-test-token", files[0].read_text(encoding="utf-8"))
            self.assertTrue(all(not record["request_truncated"] for record in records))

    def test_oversized_body_is_rejected_without_unbounded_capture(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            with patch.dict(os.environ, {"EPOCH_AUDIT_DIR": str(root / "audit"),
                                         "EPOCH_AUDIT_MAX_BODY_BYTES": "256"}):
                with TestClient(self._app(root)) as client:
                    headers = {"Authorization": "Bearer local-test-token"}
                    body = {"request_id": "large", "user_id": "u1", "session_id": "s1",
                            "messages": [{"role": "user", "content": "x" * 1024}]}
                    response = client.post("/add", json=body, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    search_response = client.post("/search", json={"query": "large response",
                                             "user_id": "u1", "top_k": 5}, headers=headers)
                    self.assertEqual(search_response.status_code, 200)
                    self.assertGreater(len(search_response.content), 256)
            files = list((root / "audit").glob("api-requests-*.jsonl"))
            records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["status"], 200)
            self.assertTrue(records[0]["request_truncated"])
            self.assertLessEqual(len(records[0]["request_body"].encode("utf-8")), 256)
            self.assertTrue(records[1]["response_truncated"])
            self.assertLessEqual(len(records[1]["response_body"].encode("utf-8")), 256)

    def test_disk_cap_stops_capture_without_affecting_api(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            with patch.dict(os.environ, {"EPOCH_AUDIT_DIR": str(root / "audit"),
                                         "EPOCH_AUDIT_MAX_DISK_BYTES": "1"}):
                with TestClient(self._app(root)) as client:
                    body = {"request_id": "cap", "user_id": "u1", "session_id": "s1",
                            "messages": [{"role": "user", "content": "short"}]}
                    response = client.post("/add", json=body,
                                           headers={"Authorization": "Bearer local-test-token"})
                    self.assertEqual(response.status_code, 200)
            audit = root / "audit"
            self.assertTrue((audit / "AUDIT_CAP_REACHED.txt").exists())
            self.assertEqual(list(audit.glob("api-requests-*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
