from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from eviepoch.config import Settings
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline


class FakeProviders:
    def __init__(self, facts: list[str] | None = None):
        self.fact_values = facts or []

    def embedding_identity(self) -> dict[str, object]:
        return {"model": "test", "dimensions": 2, "endpoint": "local"}

    def facts(self, _chunk: str) -> list[str]:
        return list(self.fact_values)

    def recall(self, _query: str, _options: list[str] | None) -> str | None:
        return None

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if lowered.startswith("fact:"):
                vector = [1.0, 0.0]
            elif "kyoto" in lowered:
                vector = [0.8, 0.6] if "i: i moved to kyoto" in lowered else [1.0, 0.0]
            elif "osaka" in lowered or "coffee" in lowered:
                vector = [0.0, 1.0]
            else:
                vector = [0.0, 1.0]
            vectors.append(vector)
        result = np.asarray(vectors, dtype=np.float32)
        return result / np.linalg.norm(result, axis=1, keepdims=True)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.config = Settings(
            db_path=str(Path(self.temp.name) / "memory.sqlite3"),
            llm_api_key="test-key",
            embed_api_key="test-key",
            embed_base_url="https://embeddings.example/v1",
            embed_dimensions=2,
            auth_scheme="none",
            recall_query=False,
            window_radius=0,
            answer_input_tokens=1000,
            answer_prompt_tokens=0,
            answer_item_tokens=1,
        )
        self.providers = FakeProviders(["FACT: Kyoto"])
        self.pipeline = MemoryPipeline(self.config, self.providers)
        self.pipeline.start()

    def tearDown(self) -> None:
        self.pipeline.close()
        self.temp.cleanup()

    def test_add_keeps_raw_turns_and_facts_and_retries_are_idempotent(self) -> None:
        request = AddRequest(
            request_id="same-request",
            user_id="person-a",
            session_id="session-a",
            messages=[
                Message("user", "I moved to Kyoto.", 1684540800),
                Message("assistant", "That sounds nice."),
            ],
        )
        self.pipeline.add(request)
        first = self.pipeline.store.for_user("person-a")

        self.assertEqual([item.kind for item in first], ["raw", "raw", "fact"])
        self.assertIn("[2023-05-20 00:00] I: I moved to Kyoto.", first[0].content)
        self.assertEqual(first[0].created_at, "2023-05-20T00:00:00Z")
        self.assertIn("[2023-05-20 00:00] FACT: Kyoto", first[2].content)

        retry = AddRequest("same-request", "person-a", "session-a", [Message("user", "different")])
        self.pipeline.add(retry)
        self.assertEqual(len(self.pipeline.store.for_user("person-a")), 3)

    def test_search_expands_neighbor_and_keeps_raw_before_higher_scoring_fact(self) -> None:
        self.config = Settings(**{
            **self.config.__dict__,
            "window_radius": 1,
        })
        self.pipeline.close()
        self.pipeline = MemoryPipeline(self.config, self.providers)
        self.pipeline.start()
        self.pipeline.add(AddRequest(
            request_id="context",
            user_id="person-a",
            session_id="session-a",
            messages=[Message("user", "I moved to Kyoto."), Message("assistant", "Coffee is ready.")],
        ))

        result = self.pipeline.search(SearchRequest("Kyoto", "person-a", 3))
        ids = [row["id"] for row in result["data"]]

        self.assertEqual(ids[0].rsplit("-", 1)[-1], "r0")
        self.assertTrue(any(item.endswith("r1") for item in ids))
        self.assertEqual(ids[-1].rsplit("-", 1)[-1], "f0")

    def test_user_scoping_and_request_ids_are_independent(self) -> None:
        for user in ("person-a", "person-b"):
            self.pipeline.add(AddRequest(
                request_id="shared-request",
                user_id=user,
                session_id="session",
                messages=[Message("user", "I moved to Kyoto.")],
            ))
        a = self.pipeline.store.for_user("person-a")
        b = self.pipeline.store.for_user("person-b")

        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 2)
        self.assertNotEqual(a[0].id, b[0].id)
        self.assertTrue(all(item.user_id == "person-a" for item in a))

    def test_search_obeys_top_k_and_query_token_budget(self) -> None:
        self.config = Settings(**{
            **self.config.__dict__,
            "answer_input_tokens": 0,
        })
        self.pipeline.close()
        self.pipeline = MemoryPipeline(self.config, self.providers)
        self.pipeline.start()
        self.pipeline.add(AddRequest(
            "budget", "person-a", "session", [Message("user", "I moved to Kyoto.")]
        ))
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto", "person-a", 10))["data"], [])
        self.assertEqual(self.pipeline.search(SearchRequest("Kyoto", "person-a", 0))["data"], [])


if __name__ == "__main__":
    unittest.main()
