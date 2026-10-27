"""Competition memory lifecycle, with a preserved B0 experimental baseline."""
from __future__ import annotations

import threading
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import IngestBusy, InvalidExtraction, Obligation, QueryPlan, Source, digest, namespace, stable_id, timestamp
from .ingest.extraction import parse_plan, validate_extraction
from .manage import reconcile
from .domain.requests import AddRequest, SearchRequest
from .providers import Providers
from .storage import Repository
from .retrieval import retrieve
from .observability.metrics import Metrics, measured


class SourceLocator(BaseModel):
    """
    EviEpoch's compact pointer to the authoritative input record.

    The source body remains in ``Source``; this model stores only locator metadata.
    Extra fields remain allowed for callers that need transport-specific pointers.
    """

    type: str | None = "chat"
    role: Literal["user", "assistant", "system", "tool"] | None = None
    chat_time: str | None = None
    message_id: str | None = None
    content: str | None = None
    doc_path: str | None = None
    file_info: dict | None = None
    image_info: dict | None = None
    model_config = ConfigDict(extra="allow")


SourceMessage = SourceLocator


def _source_batches(sources: list[Source], max_chars: int):
    """Group source fragments into extraction calls bounded by ``max_chars``."""
    batches, group, size = [], [], 0
    for source in sources:
        for start in range(0, len(source.content), max_chars):
            fragment = source.content[start:start + max_chars]
            if group and size + len(fragment) > max_chars:
                batches.append(group)
                group, size = [], 0
            group.append((source, replace(source, content=fragment)))
            size += len(fragment)
    if group:
        batches.append(group)
    return batches


def _extract_batch(providers, batch, known, max_facts):
    """Extract and validate one batch, repairing invalid output once when supported."""
    parts = [part for _source, part in batch]
    originals = [source for source, _part in batch]
    data = providers.extract(parts, known)
    try:
        return validate_extraction(data, originals, known, max_facts)
    except InvalidExtraction as exc:
        repair = getattr(providers, "repair_extraction", None)
        if repair is None:
            raise
        repaired = repair(parts, known, exc)
        return validate_extraction(repaired, originals, known, max_facts)



class MemoryPipeline:
    def __new__(cls, config, providers=None, store=None):
        if config.profile == "b0":
            from .baselines.vector import MemoryPipeline as Baseline
            return Baseline(config, providers, store)
        return super().__new__(cls)

    def __init__(self, config: Settings, providers=None, store=None):
        self.config = config
        self.metrics = Metrics()
        self.providers = providers or Providers(config)
        self.store = store or Repository(config, self.providers.embedding_identity())
        self._add_slots = threading.BoundedSemaphore(config.add_concurrency)
        self._search_slots = threading.BoundedSemaphore(config.search_concurrency)

    def start(self):
        self.store.open()

    def close(self):
        self.store.close()

    def _build_sources(self, request: AddRequest, ns: str, stored_at: str) -> list[Source]:
        sources = [
            Source(
                stable_id(ns, request.request_id, i, "source-v1"), ns, request.request_id,
                request.session_id, i, message.role, message.content, timestamp(message.timestamp), stored_at,
            )
            for i, message in enumerate(request.messages)
        ]
        for source in sources:
            message = request.messages[source.ordinal]
            source.timestamp_value = message.timestamp
            locator = SourceLocator(
                role=source.role if source.role in {"user", "assistant", "system", "tool"} else None,
                chat_time=source.asserted_at,
                message_id=source.id,
                original_role=source.role,
                request_id=source.request_id,
                ordinal=source.ordinal,
            )
            source.locator = locator.model_dump(exclude_none=True)
        return sources

    def _extract_sources(self, sources: list[Source], known):
        spans, nodes, edges, policies = [], [], [], []
        if self.config.profile != "full":
            return spans, nodes, edges, policies

        for batch in _source_batches(sources, self.config.extraction_chars):
            result = _extract_batch(self.providers, batch, known, self.config.max_facts)
            spans.extend(result[0])
            nodes.extend(result[1])
            edges.extend(result[2])
            policies.extend(result[3])
            known.nodes.update({node.id: node for node in result[1]})
            known.sources.update({source.id: source for source, _part in batch})
        return spans, nodes, edges, policies

    @measured("add")
    def add(self, request: AddRequest) -> dict:
        if not request.request_id or not request.user_id or not request.session_id or not request.messages:
            raise ValueError("identifiers and at least one source message are required")
        if any(not m.content or not m.content.strip() or not m.role for m in request.messages):
            raise ValueError("source content and role must be non-empty")
        if not self._add_slots.acquire(blocking=False):
            raise IngestBusy("Add admission limit reached")
        ns, owner = namespace(request.user_id), None
        try:
            owner = self.store.claim(ns, request.request_id, digest(asdict(request)))
            echo = {"success": True, "request_id": request.request_id,
                    "user_id": request.user_id, "session_id": request.session_id}
            if owner is None:
                return echo
            stored = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            sources = self._build_sources(request, ns, stored)
            with self.store.view(ns) as view:
                known = view.context("\n".join(s.content for s in sources))
            spans, nodes, edges, policies = self._extract_sources(sources, known)
            representations = {s.id: s.render() for s in sources}
            representations.update({n.id: n.representation() for n in nodes})
            values = self.providers.embed(list(representations.values()))
            if values.shape != (len(representations), self.config.embed_dimensions):
                raise ValueError("embedding response shape does not match objects")
            vectors = dict(zip(representations, values))
            self.store.publish(ns, request.request_id, owner, sources, spans, nodes, edges, policies, vectors,
                               partial(reconcile, consolidate=self.config.consolidation_enabled,
                                       reflect=self.config.reflection_enabled))
            return echo
        except BaseException:
            if owner is not None:
                self.store.release(ns, request.request_id, owner)
            raise
        finally:
            self._add_slots.release()

    def search(self, request: SearchRequest) -> dict:
        data, _trace = self.search_with_trace(request)
        return data

    @measured("search")
    def search_with_trace(self, request: SearchRequest):
        if request.top_k <= 0:
            return {"data": []}, {}
        if not request.query.strip() or not request.user_id:
            raise ValueError("query and user_id must be non-empty")
        if not self._search_slots.acquire(blocking=False):
            raise IngestBusy("Search admission limit reached")
        try:
            ns = namespace(request.user_id)
            with self.store.view(ns) as view:
                if not view.generation:
                    return {"data": []}, {"generation": 0, "selected_ids": []}
                if self.config.profile == "full":
                    plan = parse_plan(self.providers.plan(request.query, request.options), request.query)
                else:
                    plan = QueryPlan(["fact"], obligations=[Obligation("o1", request.query)])
                chosen, trace = retrieve(view, request, plan, self.providers, self.config)
                response = {"data": [{"id": e.id, "content": e.content, "score": float(e.score),
                                      **({"created_at": e.created_at} if e.created_at else {})} for e in chosen]}
                return response, trace
        finally:
            self._search_slots.release()

    def inspect(self, user_id: str) -> dict:
        if not self.config.workbench_enabled:
            raise PermissionError("Workbench is only enabled for public research runs")
        return self.store.inspect(namespace(user_id))
