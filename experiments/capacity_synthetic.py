"""Bounded no-provider Add/Search load check for the EviEpoch API.

Runs the real FastAPI + one-worker Uvicorn path with deterministic local fake
providers and a temporary SQLite database. It never reads .env or contacts any
LLM/embedding endpoint. Results measure application/SQLite overhead only.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import resource
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import uvicorn

sys.path.insert(0, "/srv")

from eviepoch.config import Settings
from eviepoch.models import Memory
from eviepoch.pipeline import MemoryPipeline
from eviepoch import api


DIMENSIONS = 1024
SAMPLES_PER_LEVEL = 40
LEVELS = (1, 4, 8, 16, 32)
TOKEN = "synthetic-capacity-only"


class FakeProviders:
    def embedding_identity(self) -> dict[str, object]:
        return {"model": "synthetic-local-only", "dimensions": DIMENSIONS,
                "endpoint": "loopback-fake"}

    def facts(self, chunk: str) -> list[str]:
        return ["Synthetic fact: " + chunk[:48]]

    def recall(self, query: str, _options: list[str] | None) -> str:
        return "I remember: " + query

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            raw = np.frombuffer(hashlib.sha512(text.encode("utf-8")).digest(), dtype=np.uint8)
            row = np.resize(raw.astype(np.float32) - 127.5, DIMENSIONS)
            norm = float(np.linalg.norm(row))
            rows.append(row / norm)
        return np.asarray(rows, dtype=np.float32)


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))]


def post(base: str, path: str, payload: dict) -> tuple[int, float]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": "Token " + TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    return status, (time.perf_counter() - start) * 1000


def run_wave(base: str, concurrency: int, operation: str,
             user_id: str = "synthetic-user", count: int = SAMPLES_PER_LEVEL) -> dict[str, object]:
    if operation == "add":
        payloads = [
            {"request_id": f"capacity-c{concurrency}-r{i}", "user_id": user_id,
             "session_id": f"capacity-session-{i % 4}",
             "messages": [{"role": "user", "content":
                           f"Synthetic capacity record {i}: the topic is item {i % 13}.",
                           "timestamp": 1792972800000}]}
            for i in range(SAMPLES_PER_LEVEL)
        ]
        path = "/add"
    else:
        payloads = [
            {"query": f"synthetic capacity record topic item {i % 13}",
             "user_id": user_id, "top_k": 100}
            for i in range(count)
        ]
        path = "/search"

    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda body: post(base, path, body), payloads))
    wall = time.perf_counter() - wall_start
    times = [elapsed for _status, elapsed in results]
    counts: dict[str, int] = {}
    for status, _elapsed in results:
        counts[str(status)] = counts.get(str(status), 0) + 1
    return {
        "operation": operation,
        "concurrency": concurrency,
        "requests": len(results),
        "statuses": counts,
        "p50_ms": round(percentile(times, 0.50), 2),
        "p95_ms": round(percentile(times, 0.95), 2),
        "p99_ms": round(percentile(times, 0.99), 2),
        "throughput_rps": round(len(results) / wall, 2),
    }


def main() -> None:
    start_all = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="eviepoch-capacity-") as work:
        db = Path(work) / "capacity.sqlite3"
        config = Settings(
            profile="b0",
            db_path=str(db), llm_api_key="fake-only", embed_api_key="fake-only",
            embed_base_url="https://fake.invalid/v1", embed_model="synthetic-local-only",
            embed_dimensions=DIMENSIONS, auth_scheme="token", auth_token=TOKEN,
            extract_facts=True, recall_query=True, recall_weight=0.5,
            return_limit=100, answer_input_tokens=117_760, answer_prompt_tokens=1024,
            answer_item_tokens=20, request_timeout=1.0,
        )
        api.settings = config
        api.pipeline = MemoryPipeline(config, FakeProviders())
        api.app = api.create_app(config, api.pipeline)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            api.app, host="127.0.0.1", port=port, workers=1, log_level="error",
            access_log=False,
        ))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Synthetic Uvicorn server did not start")

        base = f"http://127.0.0.1:{port}"
        print(json.dumps({"kind": "setup", "provider": "fake-local-only",
                          "database": "temporary-sqlite", "uvicorn_workers": 1,
                          "dimensions": DIMENSIONS, "samples_per_level": SAMPLES_PER_LEVEL,
                          "levels": LEVELS}, separators=(",", ":")), flush=True)
        for level in LEVELS:
            print(json.dumps(run_wave(base, level, "add"), separators=(",", ":")), flush=True)
            print(json.dumps(run_wave(base, level, "search"), separators=(",", ":")), flush=True)

        scale_count = 10_000
        rng = np.random.default_rng(20261026)
        vectors = rng.standard_normal((scale_count, DIMENSIONS)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        scale_memories = [
            Memory(
                id=f"scale-{i}", user_id="scale-user", session_id="scale-session",
                request_id="scale-seed-request", kind="raw", chunk_id=f"scale-chunk-{i}",
                content=f"Synthetic scale record {i}: topic {i % 101}.", created_at=None,
                position=i, vector=vectors[i],
            )
            for i in range(scale_count)
        ]
        api.pipeline.store.add("scale-user", "scale-seed-request", scale_memories)
        del vectors, scale_memories
        print(json.dumps({"kind": "scale_seed", "user_id": "synthetic-only",
                          "memories": api.pipeline.store.stats()["memories"],
                          "scale_memories": scale_count, "dimensions": DIMENSIONS},
                         separators=(",", ":")), flush=True)
        for level in (1, 4, 8, 16):
            print(json.dumps(run_wave(base, level, "search", user_id="scale-user", count=20),
                             separators=(",", ":")), flush=True)

        server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():
            server.force_exit = True
            raise RuntimeError("Synthetic Uvicorn server did not stop")

        size = db.stat().st_size if db.exists() else 0
        usage = resource.getrusage(resource.RUSAGE_SELF)
        print(json.dumps({"kind": "summary", "adds": SAMPLES_PER_LEVEL * len(LEVELS),
                          "searches": SAMPLES_PER_LEVEL * len(LEVELS) + 20 * 4,
                          "scale_corpus_memories": scale_count,
                          "sqlite_bytes": size, "process_peak_rss_kib": usage.ru_maxrss,
                          "elapsed_s": round(time.perf_counter() - start_all, 2)},
                         separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
