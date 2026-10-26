from __future__ import annotations

import json
import logging
import re
import threading
from urllib.parse import urlsplit

import numpy as np

from .config import Settings

log = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """The embedding provider could not complete a request."""

EXTRACT_PROMPT = """You turn a chunk of a conversation into atomic memories for a personal memory index.

Rules:
1. One fact per line. Each memory must stand alone: no pronouns without a named referent, no "the above", no cross-references.
2. Keep every specific name, place, title, number, quantity and date exactly as written. Never generalise ("Rob", not "a colleague").
3. Write the memory owner's own statements in the first person ("I ..."). Attribute the other party's statements to their name when known, otherwise to "the other person".
4. Record what was said or done, including preferences, plans, opinions, feelings and events. Do not infer anything that is not supported by the text.
5. If the message carries a date, start the memory with that date in brackets, e.g. "[2023-05-20] I adopted a beagle named Ollie."
6. Do not answer questions, summarise, or editorialise. No commentary.

Return JSON only: {"facts": ["...", "..."]}. At most %d facts. Return {"facts": []} if there is nothing worth remembering."""

RECALL_PROMPT = """You write the memory-check question a person would ask their assistant about their own past conversations.

Given a question that will be answered from someone's personal memory log, write ONE short question in that person's own first-person voice, in the register of a chat log, e.g. "Did I tell you about ...?" or "What did I say about ...?".

Rules:
1. First person, the user's own voice. Never address the user as "you".
2. Name the concrete entities, people, places and time expressions that the memory would contain.
3. Ask about the memory, do not answer the question and do not guess the answer.
4. If answer options are given, treat them only as a source of topic words; never assume any option is true.
5. One line, no preamble, no quotes."""

_REASONING = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_BULLET = re.compile(r"^(?:[-*]|\d+[.)]|\[)")


def parse_facts(text: str, limit: int) -> list[str]:
    text = _REASONING.sub("", text)
    if "<think>" in text:
        return []
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0)).get("facts", [])
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()][:limit]
        except (json.JSONDecodeError, AttributeError):
            pass
    facts = []
    for line in text.splitlines():
        item = line.strip()
        if not _BULLET.match(item):
            continue
        item = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", item).strip(' "')
        if len(item) > 3:
            facts.append(item)
    return facts[:limit]


def _endpoint(value: str, name: str) -> str:
    if not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} must be an explicit HTTP(S) endpoint")
    parts = urlsplit(value)
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment):
        raise ValueError(f"{name} must be HTTP(S) without credentials, query, or fragment")
    return value.rstrip("/")


def _unit(values: object, dimensions: int) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (dimensions,) or not np.isfinite(vector).all():
        raise ValueError("Embedding response has invalid dimensions or values")
    scale = float(np.max(np.abs(vector)))
    if scale == 0:
        raise ValueError("Embedding response is a zero vector")
    vector = vector / scale
    return np.asarray(vector / np.linalg.norm(vector), dtype=np.float32)


def _segments(text: str, max_bytes: int = 2048) -> list[str]:
    if not isinstance(text, str) or not text:
        raise ValueError("Embedding text must be non-empty")
    output: list[str] = []
    start = size = 0
    for position, char in enumerate(text):
        byte_count = len(char.encode("utf-8"))
        if size + byte_count > max_bytes:
            output.append(text[start:position])
            start, size = position, 0
        size += byte_count
    output.append(text[start:])
    return output


class Providers:
    """Two independent OpenAI-compatible clients behind a small testable interface."""

    def __init__(self, config: Settings):
        self.config = config
        self._chat_client = None
        self._embed_client = None
        self._lock = threading.Lock()

    def embedding_identity(self) -> dict[str, object]:
        return {
            "model": self.config.embed_model,
            "dimensions": self.config.embed_dimensions,
            "endpoint": (_endpoint(self.config.embed_base_url, "EPOCH_EMBED_BASE_URL")
                         if self.config.embed_base_url else ""),
            "preprocessing": "utf8-2048-byte-byte-weighted-normalized-mean",
        }

    def _client(self, embedding: bool):
        attr = "_embed_client" if embedding else "_chat_client"
        with self._lock:
            current = getattr(self, attr)
            if current is not None:
                return current
            from openai import OpenAI

            if embedding:
                if not self.config.embed_api_key:
                    raise RuntimeError("EPOCH_EMBED_API_KEY is required")
                client = OpenAI(
                    api_key=self.config.embed_api_key,
                    base_url=_endpoint(self.config.embed_base_url, "EPOCH_EMBED_BASE_URL"),
                    timeout=self.config.request_timeout,
                    max_retries=1,
                )
            else:
                if not self.config.llm_api_key:
                    return None
                args = {"api_key": self.config.llm_api_key, "timeout": self.config.request_timeout,
                        "max_retries": 1}
                if self.config.llm_base_url:
                    args["base_url"] = _endpoint(self.config.llm_base_url, "EPOCH_LLM_BASE_URL")
                client = OpenAI(**args)
            setattr(self, attr, client)
            return client

    def _complete(self, system: str, user: str, max_tokens: int) -> str | None:
        if not self.config.llm_enabled:
            return None
        try:
            response = self._client(False).chat.completions.create(
                model=self.config.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content or ""
            text = _REASONING.sub("", text)
            return "" if "<think>" in text else text.strip()
        except Exception:
            log.warning("chat request failed")
            return None

    def facts(self, chunk: str) -> list[str]:
        raw = self._complete(EXTRACT_PROMPT % self.config.max_facts, chunk, 1200)
        return parse_facts(raw, self.config.max_facts) if raw else []

    def recall(self, query: str, options: list[str] | None) -> str | None:
        user = f"Question: {query}"
        if options:
            user += "\nAnswer options: " + " | ".join(str(value) for value in options[:10])
        raw = self._complete(RECALL_PROMPT, user, 200)
        return raw.splitlines()[0].strip(' "') if raw and raw.splitlines() else None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.config.embed_dimensions), dtype=np.float32)
        segments: list[str] = []
        segment_owner: list[tuple[int, int]] = []
        for owner, value in enumerate(texts):
            for part in _segments(value):
                segments.append(part)
                segment_owner.append((owner, len(part.encode("utf-8"))))
        response_vectors: list[np.ndarray | None] = [None] * len(segments)
        client = self._client(True)
        batch_size = 10
        for start in range(0, len(segments), batch_size):
            block = segments[start:start + batch_size]
            try:
                response = client.embeddings.create(
                    model=self.config.embed_model,
                    input=block,
                    dimensions=self.config.embed_dimensions,
                    encoding_format="float",
                )
            except Exception as exc:
                log.warning("embedding request failed")
                raise ProviderUnavailable("embedding service unavailable") from exc
            if len(response.data) != len(block):
                raise ValueError("Embedding service omitted one or more inputs")
            for item in response.data:
                offset = item.index
                if not 0 <= offset < len(block) or response_vectors[start + offset] is not None:
                    raise ValueError("Embedding service returned duplicate or invalid indices")
                response_vectors[start + offset] = _unit(item.embedding, self.config.embed_dimensions)

        pooled = np.zeros((len(texts), self.config.embed_dimensions), dtype=np.float64)
        weights = np.zeros(len(texts), dtype=np.float64)
        for vector, (owner, size) in zip(response_vectors, segment_owner):
            if vector is None:
                raise ValueError("Embedding service omitted a segment")
            pooled[owner] += vector * size
            weights[owner] += size
        return np.stack([_unit(row / weights[pos], self.config.embed_dimensions)
                         for pos, row in enumerate(pooled)])
