"""Public synthetic concurrency probe; no models, credentials, server or private data."""
import argparse
import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_lifecycle import AnnotatedProviders
from eviepoch.config import Settings
from eviepoch.domain.requests import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from eviepoch.retrieval.tokens import count


def probe(workers=8, delay=0.1):
    class SlowProvider(AnnotatedProviders):
        def __init__(self):
            super().__init__()
            self.guard, self.active, self.peak = threading.Lock(), 0, 0

        def plan(self, query, options):
            with self.guard:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(delay)
                return super().plan(query, options)
            finally:
                with self.guard:
                    self.active -= 1

    count("prewarm tokenizer")
    with tempfile.TemporaryDirectory(prefix="eviepoch-offline-") as folder:
        provider = SlowProvider()
        config = Settings(db_path=str(Path(folder) / "probe.sqlite3"), embed_dimensions=32,
                          llm_api_key="synthetic", auth_scheme="none", gap_iterations=0)
        memory = MemoryPipeline(config, provider)
        memory.start()
        try:
            for i in range(workers):
                memory.add(AddRequest("seed", f"synthetic-{i}", "session", [Message("user", "Favorite shade is teal.")]))
            barrier = threading.Barrier(workers)
            def query(i):
                barrier.wait(timeout=10)
                started = time.perf_counter()
                result = memory.search(SearchRequest("favorite shade", f"synthetic-{i}", 3))
                return {"elapsed_ms": round((time.perf_counter()-started)*1000, 2), "hits": len(result["data"])}
            started = time.perf_counter()
            with ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(query, range(workers)))
            return {"official_score": False, "scope": "synthetic pipeline with simulated model delay; not HTTP/provider capacity",
                    "workers": workers, "simulated_plan_ms": delay*1000, "peak_concurrent_plan_calls": provider.peak,
                    "wall_ms": round((time.perf_counter()-started)*1000, 2), "results": results, "network_calls": 0,
                    "passed": provider.peak == workers and all(r["hits"] for r in results)}
        finally:
            memory.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/offline-concurrency.json"))
    args = parser.parse_args()
    report = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
