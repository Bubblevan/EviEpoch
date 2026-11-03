"""Competition memory lifecycle, with a preserved B0 experimental baseline."""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .adapters import get_task_adapter
from .config import Settings
from .domain import (IngestBusy, InvalidExtraction, Obligation, PublishConflict, QueryPlan, Source,
                     annotate_span, chunk_source, digest, namespace, stable_id, timestamp)
from .ingest.extraction import parse_plan, validate_extraction
from .manage import reconcile
from .manage.temporal import verify_changes
from .ingest.temporal import message_anchor
from .ingest.structured import structured_nodes
from .domain.requests import AddRequest, SearchRequest, add_payload_digest
from .providers import Providers, ProviderUnavailable, StructuredOutputTruncated, StructuredOutputInvalid
from .storage import Repository
from .retrieval import retrieve
from .observability.metrics import Metrics, measured
from .observability.diagnostics import log_dataflow_event, log_ingest_degraded, log_pipeline_failure


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
            part = replace(source, content=fragment, locator={**source.locator,
                           "extraction_char_start": start, "extraction_char_end": start + len(fragment)})
            group.append((source, part))
            size += len(fragment)
    if group:
        batches.append(group)
    return batches


def _extract_batch(providers, batch, known, max_facts, consume=lambda: None):
    """Extract and validate one batch, repairing invalid output once when supported."""
    parts = [part for _source, part in batch]
    originals = [source for source, _part in batch]
    windows = [(p.locator.get("extraction_char_start", 0), p.locator.get("extraction_char_end", len(s.content)))
               for s, p in batch]
    consume()
    data = providers.extract(parts, known)
    try:
        return validate_extraction(data, originals, known, max_facts, windows)
    except InvalidExtraction as exc:
        exc.stage = "extract_validation"
        repair = getattr(providers, "repair_extraction", None)
        if repair is None:
            raise
        consume()
        repaired = repair(parts, known, exc, previous=data)
        try:
            return validate_extraction(repaired, originals, known, max_facts, windows)
        except InvalidExtraction as repair_error:
            repair_error.stage = "extract_repair_validation"
            raise


def _split_batch(batch):
    if len(batch) > 1:
        middle = len(batch) // 2
        return batch[:middle], batch[middle:]
    source, part = batch[0]
    if len(part.content) < 512:
        return None
    middle = len(part.content) // 2
    overlap = min(128, middle // 4)
    start = part.locator.get("extraction_char_start", 0)
    result = []
    for left, right in ((0, middle + overlap), (middle - overlap, len(part.content))):
        fragment = replace(part, content=part.content[left:right], locator={**part.locator,
                           "extraction_char_start": start + left, "extraction_char_end": start + right})
        result.append([(source, fragment)])
    return result


def _span_representation(span):
    # Keep each passage under the embedding provider's segmentation threshold so
    # it receives one vector instead of a pooled representation.
    return span.quote


def _source_representation(source):
    rendered = source.render()
    return rendered if len(rendered.encode("utf-8")) <= 2048 else source.content



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
        self.adapter = get_task_adapter(config)
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
            if message.envelope is not None:
                source.locator["content_envelope"] = message.envelope.to_locator_dict(staging=True)
            if self.config.temporal_anchors_enabled:
                source.locator["temporal_anchor"] = message_anchor(source)
        return sources

    def _extract_sources(self, sources: list[Source], known):
        spans, nodes, edges, policies = [], [], [], []
        if self.config.profile != "full":
            return spans, nodes, edges, policies

        model_sources = []
        for source in sources:
            extracted, anchors = structured_nodes(source, known) if self.config.structured_ingest_enabled else ([], [])
            if extracted:
                nodes.extend(extracted)
                spans.extend(anchors)
                known.nodes.update({node.id: node for node in extracted})
                known.sources[source.id] = source
            else:
                model_sources.append(source)

        deadline, calls = time.monotonic() + self.config.extraction_deadline_seconds, 0

        def consume():
            nonlocal calls
            if calls >= self.config.extraction_max_calls or time.monotonic() >= deadline:
                raise ProviderUnavailable("bounded extraction budget exhausted", stage="extract_budget")
            calls += 1

        def ingest(batch, depth=0):
            try:
                result = _extract_batch(self.providers, batch, known, self.config.max_facts, consume)
            except (InvalidExtraction, StructuredOutputTruncated, StructuredOutputInvalid) as exc:
                split = _split_batch(batch) if depth < self.config.extraction_split_depth else None
                if split is None:
                    if isinstance(exc, InvalidExtraction):
                        log_pipeline_failure(stage=getattr(exc, "stage", None) or "extract_validation", error=exc)
                    raise
                for child in split:
                    ingest(child, depth + 1)
                return
            if any(n.change in {"correction", "retraction"} for n in result[1]) and hasattr(self.providers, "verify_changes"):
                consume()
            try:
                checked_nodes, checked_edges = verify_changes(result[1], result[2], [s for s, _part in batch], known, self.providers)
            except (InvalidExtraction, ProviderUnavailable) as exc:
                if not getattr(exc, "stage", None):
                    exc.stage = "change_verification"
                log_pipeline_failure(stage=exc.stage, error=exc)
                raise
            result = (result[0], checked_nodes, checked_edges, result[3])
            spans.extend(result[0])
            nodes.extend(result[1])
            edges.extend(result[2])
            policies.extend(result[3])
            known.nodes.update({node.id: node for node in result[1]})
            known.sources.update({source.id: source for source, _part in batch})

        for batch in _source_batches(model_sources, self.config.extraction_chars):
            ingest(batch)
        if self.config.episode_navigation_enabled:
            from .manage.episodes import episodes
            nodes.extend(episodes(sources, nodes))
        merged_policies = {}
        for policy in policies:
            key = (policy.action, policy.instruction_source_id, policy.subject, policy.predicate, policy.scope)
            if key in merged_policies:
                policy.source_ids = sorted(set(policy.source_ids + merged_policies[key].source_ids))
            merged_policies[key] = policy
        return (list({s.id: s for s in spans}.values()), list({n.id: n for n in nodes}.values()),
                list({e.id: e for e in edges}.values()), list(merged_policies.values()))

    @measured("add")
    def add(self, request: AddRequest) -> dict:
        request = self.adapter.normalize_add(request)
        if self.config.task_adapter != "multimodal":
            for message in request.messages:
                if isinstance(message.content, list) or message.envelope is not None:
                    raise ValueError("ordered content arrays require EPOCH_TASK_ADAPTER=multimodal")
        if not request.request_id or not request.user_id or not request.session_id or not request.messages:
            raise ValueError("identifiers and at least one source message are required")
        if any(not m.role for m in request.messages):
            raise ValueError("source role must be non-empty")
        if any(not m.content or not str(m.content).strip() for m in request.messages):
            raise ValueError("source content must be non-empty after normalization")
        if not self._add_slots.acquire(blocking=False):
            raise IngestBusy("Add admission limit reached")
        ns, owner = namespace(request.user_id), None
        try:
            owner = self.store.claim(ns, request.request_id, add_payload_digest(request))
            echo = {"success": True, "request_id": request.request_id,
                    "user_id": request.user_id, "session_id": request.session_id}
            if owner is None:
                log_dataflow_event("add_idempotent_replay", backend=("postgresql" if self.store.pg else "sqlite"),
                                   request_id=request.request_id, response=echo)
                return echo
            stored = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            sources = self._build_sources(request, ns, stored)
            log_dataflow_event("add_sources_materialized", backend=("postgresql" if self.store.pg else "sqlite"),
                               request_id=request.request_id, namespace=ns,
                               sources=[asdict(source) for source in sources])
            with self.store.view(ns) as view:
                known = view.context("\n".join(s.content for s in sources))
            log_dataflow_event("add_prior_context", namespace=ns, generation=view.generation,
                               source_count=len(known.sources), node_count=len(known.nodes),
                               edge_count=len(known.edges))
            try:
                spans, nodes, edges, policies = self._extract_sources(sources, known)
                extension = self.adapter.extend_ingest(sources, known, self.providers)
                spans.extend(extension.spans)
                nodes.extend(extension.nodes)
                edges.extend(extension.edges)
                self._pending_coding_artifacts = extension.artifacts
            except (InvalidExtraction, ProviderUnavailable) as exc:
                if not self.config.raw_ingest_fallback:
                    raise
                # Preserve the authoritative messages but publish no unvalidated
                # model-derived facts, spans, relations, or governance decisions.
                spans, nodes, edges, policies = [], [], [], []
                extension = self.adapter.extend_ingest(sources, known, self.providers)
                spans.extend(extension.spans)
                nodes.extend(extension.nodes)
                edges.extend(extension.edges)
                self._pending_coding_artifacts = extension.artifacts
                log_ingest_degraded(error=exc, source_count=len(sources))
            log_dataflow_event("add_extraction_and_governance", request_id=request.request_id,
                               spans=[asdict(item) for item in spans], nodes=[asdict(item) for item in nodes],
                               edges=[asdict(item) for item in edges],
                               policies=[asdict(item) for item in policies])
            spans = list({span.id: span for span in
                          [annotate_span(span) for span in spans] + [span for source in sources
                           for span in chunk_source(source, self.config.source_chunk_bytes)]}.values())
            for attempt in range(3):
                base_generation, prepared_nodes, prepared_edges, missing_node_vectors = self.store.prepare_publish(
                    ns, nodes, edges, partial(reconcile, consolidate=self.config.consolidation_enabled,
                                              reflect=self.config.reflection_enabled))
                log_dataflow_event("add_reconciled_projection", backend=("postgresql" if self.store.pg else "sqlite"),
                                   request_id=request.request_id, base_generation=base_generation,
                                   prepared_nodes=[asdict(item) for item in prepared_nodes],
                                   prepared_edges=[asdict(item) for item in prepared_edges],
                                   missing_node_vector_ids=sorted(missing_node_vectors))
                chunked_sources = {source.id for source in sources
                                   if len(source.content.encode("utf-8")) > self.config.source_chunk_bytes}
                representations = {s.id: _source_representation(s) for s in sources if s.id not in chunked_sources}
                representations.update({span.id: _span_representation(span) for span in spans
                                        if len(span.quote.encode("utf-8")) <= self.config.source_chunk_bytes})
                representations.update({n.id: n.representation() for n in prepared_nodes
                                        if n.id in missing_node_vectors})
                coding_artifacts = getattr(self, "_pending_coding_artifacts", [])
                for artifact in coding_artifacts:
                    if artifact.vector_text and artifact.id not in representations:
                        representations[artifact.id] = artifact.vector_text
                values = self.providers.embed(list(representations.values()))
                if values.shape != (len(representations), self.config.embed_dimensions):
                    raise ValueError("embedding response shape does not match objects")
                vectors = dict(zip(representations, values))
                vector_metadata = []
                for object_id, vector in vectors.items():
                    normalized_bytes = vector.astype("float32", copy=False).tobytes()
                    vector_metadata.append({"object_id": object_id, "dimensions": int(vector.shape[0]),
                                            "sha256": hashlib.sha256(normalized_bytes).hexdigest()})
                log_dataflow_event("add_embedding_vectors_ready", model=self.config.embed_model,
                                   dimensions=self.config.embed_dimensions,
                                   indexed_texts=[{"object_id": object_id, "text": text}
                                                  for object_id, text in representations.items()],
                                   vector_metadata=vector_metadata)
                try:
                    generation = self.store.publish(ns, request.request_id, owner, sources, spans, prepared_nodes,
                                                    prepared_edges, policies, vectors, base_generation,
                                                    coding_artifacts=coding_artifacts)
                    log_dataflow_event("add_database_commit", backend=("postgresql" if self.store.pg else "sqlite"),
                                       request_id=request.request_id, namespace=ns, generation=generation,
                                       policy_count=len(policies), source_count=len(sources), span_count=len(spans),
                                       node_count=len(prepared_nodes), edge_count=len(prepared_edges),
                                       vector_count=len(vectors), response=echo)
                    break
                except PublishConflict:
                    if attempt == 2:
                        raise IngestBusy("namespace kept changing while preparing derived indexes; retry Add")
            return echo
        except BaseException as exc:
            log_dataflow_event("add_failed", request_id=request.request_id,
                               error_type=type(exc).__name__, failure_stage=getattr(exc, "stage", None))
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
            log_dataflow_event("search_response", backend=("postgresql" if self.store.pg else "sqlite"),
                               query=request.query, response={"data": []}, reason="top_k_zero")
            return {"data": []}, {}
        if isinstance(request.query, list) and self.config.task_adapter != "multimodal":
            raise ValueError("ordered query arrays require EPOCH_TASK_ADAPTER=multimodal")
        if not request.query_text.strip() or not request.user_id:
            raise ValueError("query and user_id must be non-empty")
        if not self._search_slots.acquire(blocking=False):
            raise IngestBusy("Search admission limit reached")
        try:
            ns = namespace(request.user_id)
            log_dataflow_event("search_request", backend=("postgresql" if self.store.pg else "sqlite"),
                               namespace=ns, query=request.query, options=request.options, top_k=request.top_k)
            for attempt in range(self.config.search_consistency_retries + 1):
                view = self.store.read_session(ns)
                if not view.generation:
                    response = {"data": []}
                    trace = {"generation": 0, "selected_ids": []}
                    log_dataflow_event("search_response", backend=("postgresql" if self.store.pg else "sqlite"),
                                       generation=0, query=request.query, response=response,
                                       retrieval_trace=trace)
                    return response, trace
                planning_started = time.perf_counter()
                if self.config.profile == "full":
                    draft = self.providers.plan(request.query_text, request.options)
                    try:
                        plan = parse_plan(draft, request.query_text)
                    except InvalidExtraction as error:
                        repair = getattr(self.providers, "repair_plan", None)
                        if repair is None:
                            raise
                        # One source-free schema repair; the query/options remain data.
                        revised = repair(request.query_text, request.options, draft, str(error))
                        plan = parse_plan(revised, request.query_text)
                else:
                    plan = QueryPlan(["fact"], obligations=[Obligation("o1", request.query_text)])
                plan = self.adapter.extend_plan(plan, request.query)
                log_dataflow_event("search_query_plan", query=request.query,
                                   plan=asdict(plan), planning_ms=round((time.perf_counter() - planning_started) * 1000, 2))
                planning_ms = (time.perf_counter() - planning_started) * 1000
                chosen, trace = retrieve(view, request, plan, self.providers, self.config, task_adapter=self.adapter)
                chosen, coding_trace = self.adapter.extend_evidence(view, request, plan, chosen)
                trace.update(coding_trace)
                visual = coding_trace.get("multimodal") or {}
                visual_ids = [
                    item["id"] for item in visual.get("obligations") or []
                    if isinstance(item, dict) and item.get("id")
                ]
                if visual_ids:
                    covered = [item for item in trace.get("covered_obligations", []) if item not in visual_ids]
                    missing = [item for item in trace.get("missing_obligations", []) if item not in visual_ids]
                    status = visual.get("obligation_status")
                    if isinstance(status, dict):
                        for item_id in visual_ids:
                            if status.get(item_id):
                                covered.append(item_id)
                            else:
                                missing.append(item_id)
                    elif visual.get("visual_obligation_complete"):
                        covered.extend(visual_ids)
                    else:
                        missing.extend(visual_ids)
                    trace["covered_obligations"] = covered
                    trace["missing_obligations"] = missing
                trace.setdefault("timings_ms", {})["planning"] = planning_ms
                trace["timings_ms"].update(view.timings_ms)
                response = {"data": [{"id": e.id, "content": e.content, "score": float(e.score),
                                      **({"created_at": e.created_at} if e.created_at else {}),
                                      **({"source_id": e.source_id,
                                          "source_spans": list(e.source_spans),
                                          "source_ranges": [list(r) for r in e.source_ranges],
                                          "source_paragraphs": list(e.source_paragraphs)}
                                         if e.source_ranges else {})} for e in chosen]}
                # Linearization point: no model work or response construction holds this lock.
                # A concurrent publish (including forget) invalidates the entire optimistic read.
                with self.store.view(ns) as current:
                    consistent = ((current.generation, current.policy_generation) ==
                                  (view.generation, view.policy_generation))
                    if consistent:
                        trace["consistency_retries"] = attempt
                        log_dataflow_event(
                            "search_response", backend=("postgresql" if self.store.pg else "sqlite"),
                            namespace=ns, query=request.query, top_k=request.top_k,
                            retrieval_trace=trace, evidence=[asdict(item) for item in chosen], response=response,
                        )
                        from .observability.diagnostics import log_search_result
                        log_search_result(trace)
                        return response, trace
            raise ProviderUnavailable("memory changed during bounded retrieval retries", stage="read_consistency")
        finally:
            self._search_slots.release()

    def inspect(self, user_id: str) -> dict:
        if not self.config.workbench_enabled:
            raise PermissionError("Workbench is only enabled for public research runs")
        return self.store.inspect(namespace(user_id))
