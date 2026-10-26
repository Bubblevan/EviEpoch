from __future__ import annotations

import datetime as dt

import numpy as np

from . import tokens
from .config import Settings
from .models import AddRequest, Memory, SearchRequest
from .providers import Providers
from .storage import SQLiteMemoryStore, chunk_id, memory_id

def stamp(timestamp: int | None) -> tuple[str | None, str]:
    if timestamp is None:
        return None, ""
    if 0 < timestamp < 100_000_000_000:
        timestamp *= 1000
    try:
        moment = dt.datetime.fromtimestamp(timestamp / 1000, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, ""
    return moment.isoformat().replace("+00:00", "Z"), moment.strftime("[%Y-%m-%d %H:%M] ")


def content_key(text: str) -> str:
    return " ".join(text.lower().split())[:120]


class MemoryPipeline:
    """The experiment's only path: dual-channel Add, fused Search, evidence-only output."""

    def __init__(self, config: Settings, providers: Providers | None = None,
                 store: SQLiteMemoryStore | None = None):
        self.config = config
        self.providers = providers or Providers(config)
        self.store = store or SQLiteMemoryStore(config.db_path, config.embed_dimensions,
                                                self.providers.embedding_identity())

    def start(self) -> None:
        self.store.open()

    def close(self) -> None:
        self.store.close()

    def add(self, request: AddRequest) -> dict:
        echo = {"success": True, "request_id": request.request_id,
                "user_id": request.user_id, "session_id": request.session_id}
        with self.store.request_lock(request.user_id, request.request_id):
            if self.store.has_request(request.user_id, request.request_id):
                return echo
            chunk = chunk_id(request.user_id, request.request_id)
            raw: list[Memory] = []
            lines: list[str] = []
            prefix = ""
            for message in request.messages:
                if message.timestamp is not None:
                    prefix = stamp(message.timestamp)[1]
                    break
            for position, message in enumerate(request.messages):
                body = (message.content or "").strip()
                if not body:
                    continue
                created_at, own_prefix = stamp(message.timestamp)
                speaker = "I" if message.role == "user" else (message.role or "other").capitalize()
                raw.append(Memory(
                    id=memory_id(chunk, "raw", position),
                    user_id=request.user_id,
                    session_id=request.session_id,
                    request_id=request.request_id,
                    kind="raw",
                    chunk_id=chunk,
                    content=f"{own_prefix}{speaker}: {body}",
                    created_at=created_at,
                    position=position,
                    vector=np.empty((0,), dtype=np.float32),
                ))
                lines.append(f"{own_prefix}{message.role}: {body}")

            memories = list(raw)
            if lines and self.config.extract_facts and self.config.llm_enabled:
                facts = self.providers.facts("\n".join(lines))
                first_created = raw[0].created_at if raw else None
                for position, fact in enumerate(facts[:self.config.max_facts]):
                    text = fact if fact.startswith("[") else f"{prefix}{fact}"
                    memories.append(Memory(
                        id=memory_id(chunk, "fact", position),
                        user_id=request.user_id,
                        session_id=request.session_id,
                        request_id=request.request_id,
                        kind="fact",
                        chunk_id=chunk,
                        content=text,
                        created_at=first_created,
                        position=position,
                        vector=np.empty((0,), dtype=np.float32),
                    ))
            if not memories:
                return echo

            vectors = self.providers.embed([memory.content for memory in memories])
            if vectors.shape != (len(memories), self.config.embed_dimensions):
                raise ValueError("Embedding response shape does not match Add memories")
            for memory, vector in zip(memories, vectors):
                memory.vector = vector
            self.store.add(request.user_id, request.request_id, memories)
        return echo

    def search(self, request: SearchRequest) -> dict:
        if request.top_k <= 0:
            return {"data": []}
        memories = self.store.for_user(request.user_id)
        if not memories:
            return {"data": []}

        recall = None
        if self.config.recall_query and self.config.llm_enabled:
            recall = self.providers.recall(request.query, request.options)
        queries = [request.query] + ([recall] if recall else [])
        vectors = self.providers.embed(queries)
        matrix = np.stack([item.vector for item in memories])
        scores = matrix @ vectors[0]
        if recall:
            weight = self.config.recall_weight
            scores = (1.0 - weight) * scores + weight * (matrix @ vectors[1])

        token_budget = tokens.budget(self.config, request.query, request.options)
        chosen = self._select(memories, scores, request.top_k, token_budget)
        return {"data": [
            {"id": item.id, "content": item.content, "score": score,
             **({"created_at": item.created_at} if item.created_at else {})}
            for item, score in chosen
        ]}

    def _select(self, memories: list[Memory], scores: np.ndarray, top_k: int,
                token_budget: int) -> list[tuple[Memory, float]]:
        limit = min(top_k, self.config.return_limit)
        if limit <= 0 or self.config.char_budget <= 0 or token_budget <= 0:
            return []
        order = sorted(range(len(memories)), key=lambda row: (-float(scores[row]), row))
        chosen: list[tuple[Memory, float]] = []
        seen: set[str] = set()
        chars_left = self.config.char_budget
        tokens_left = token_budget

        def take(item: Memory, score: float) -> bool:
            nonlocal chars_left, tokens_left
            key = content_key(item.content)
            if key in seen:
                return False
            if len(item.content) > chars_left and chosen:
                return False
            cost = tokens.item_cost(self.config, item.content)
            if cost > tokens_left:
                return False
            seen.add(key)
            chars_left -= len(item.content)
            tokens_left -= cost
            chosen.append((item, score))
            return len(chosen) >= limit or chars_left <= 0 or tokens_left <= self.config.answer_item_tokens

        by_chunk: dict[str, list[Memory]] = {}
        for item in memories:
            if item.kind == "raw":
                by_chunk.setdefault(item.chunk_id, []).append(item)
        for rows in by_chunk.values():
            rows.sort(key=lambda item: item.position)

        for row in order:
            item, score = memories[row], float(scores[row])
            if take(item, score):
                break
            if item.kind != "raw" or self.config.window_radius <= 0:
                continue
            adjacent = [other for other in by_chunk.get(item.chunk_id, ())
                        if 0 < abs(other.position - item.position) <= self.config.window_radius]
            adjacent.sort(key=lambda other: other.position)
            for other in adjacent:
                if take(other, score):
                    break
            if len(chosen) >= limit or chars_left <= 0 or tokens_left <= self.config.answer_item_tokens:
                break

        if self.config.raw_first:
            chosen.sort(key=lambda pair: pair[0].kind != "raw")
        return chosen
