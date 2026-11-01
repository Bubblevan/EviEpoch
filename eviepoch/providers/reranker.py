"""HTTP adapter for the independent cross-encoder reranker service."""
from __future__ import annotations

import time

import numpy as np

from ..observability.diagnostics import log_provider_failure, log_provider_success
from .errors import ProviderUnavailable


class RerankerClient:
    def __init__(self, config):
        self.config = config

    def rerank(self, query: str, sources) -> dict[str, float]:
        import httpx
        started = time.perf_counter()
        try:
            if not self.config.rerank_enabled or not self.config.rerank_url:
                raise RuntimeError("reranker is disabled or has no endpoint")
            timeout = httpx.Timeout(self.config.rerank_timeout_seconds,
                                    connect=min(3.0, self.config.rerank_timeout_seconds))
            def segments(text: str) -> list[str]:
                size = self.config.rerank_segment_chars
                count = max(1, (len(text) + size - 1) // size)
                keep = min(count, self.config.rerank_max_segments)
                if count <= keep:
                    indices = list(range(count))
                elif keep == 1:
                    indices = [count // 2]
                else:
                    indices = sorted({round(i * (count - 1) / (keep - 1)) for i in range(keep)})
                return [text[index * size:(index + 1) * size] for index in indices]

            response = httpx.post(self.config.rerank_url, json={"query": query, "documents": [
                {"id": sid, "segments": segments(source.content)} for sid, source in sources.items()]},
                timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("scores") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("reranker response requires a scores array")
            scores = {}
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                        or isinstance(row.get("score"), bool)
                        or not isinstance(row.get("score"), (int, float))):
                    raise ValueError("reranker returned a malformed score")
                sid, score = row["id"], float(row["score"])
                if sid in scores or not np.isfinite(score):
                    raise ValueError("reranker returned a duplicate ID or non-finite score")
                scores[sid] = score
            if set(scores) != set(sources):
                raise ValueError("reranker must return exactly the supplied candidate IDs with finite scores")
            log_provider_success(stage="rerank", duration_ms=(time.perf_counter() - started) * 1000)
            return scores
        except Exception as exc:
            log_provider_failure(stage="rerank", error=exc,
                                 duration_ms=(time.perf_counter() - started) * 1000)
            raise ProviderUnavailable("configured reranker unavailable", stage="rerank") from exc
