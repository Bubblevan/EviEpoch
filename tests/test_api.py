import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from eviepoch.api import create_app
from eviepoch.config import Settings
from eviepoch.pipeline import MemoryPipeline
from test_lifecycle import AnnotatedProviders, fact


class ApiTests(unittest.TestCase):
    def test_auth_contract_conflict_health_and_private_workbench_disabled(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            config = Settings(db_path=str(Path(folder) / "api.sqlite3"), llm_api_key="test", embed_api_key="test",
                              embed_base_url="https://example.test/v1", embed_dimensions=32, auth_token="local-test-token")
            providers = AnnotatedProviders()
            providers.register("My address is Kyoto.", [fact("My address is Kyoto.", "Kyoto")])
            app = create_app(config, MemoryPipeline(config, providers))
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/ready").status_code, 200)
                body = {"request_id": " r ", "user_id": " u ", "session_id": " s ",
                        "messages": [{"role": "user", "content": "My address is Kyoto."}]}
                self.assertEqual(client.post("/add", json=body).status_code, 401)
                headers = {"Authorization": "Bearer local-test-token"}
                response = client.post("/add", json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"success": True, "request_id": " r ", "user_id": " u ", "session_id": " s "})
                self.assertEqual(client.post("/add", json=body, headers=headers).status_code, 200)
                body["messages"][0]["content"] = "Changed payload"
                self.assertEqual(client.post("/add", json=body, headers=headers).status_code, 409)
                result = client.post("/search", json={"query": "Kyoto address", "user_id": " u ", "top_k": 100}, headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(len(result.json()["data"]), 1)
                self.assertEqual(client.get("/research/memory?user_id=u", headers=headers).status_code, 404)
                body["messages"][0]["content"] = "  "
                self.assertEqual(client.post("/add", json=body, headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
