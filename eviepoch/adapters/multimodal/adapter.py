"""Multimodal task adapter (TRD §20)."""
from __future__ import annotations

from ...domain import QueryPlan, Source
from ...domain.content import SourceEnvelope, image_urls, normalize_content, precheck_data_uris, validate_media_limits
from ...domain.requests import AddRequest, Message, SearchRequest
from ...retrieval.evidence import Evidence
from ...storage.view import View
from ..base import IngestExtension, SearchExtension, TaskAdapter
from .controller import apply_multimodal_evidence, run_multimodal_search
from .ingest import ingest_sources
from .search import plan_obligations


class MultimodalAdapter(TaskAdapter):
    def __init__(self, config):
        super().__init__(config)
        self._search_state = None

    def normalize_add(self, request: AddRequest) -> AddRequest:
        urls: list[str] = []
        for message in request.messages:
            if message.envelope is not None:
                urls.extend(image_urls(message.envelope.parts))
            elif isinstance(message.content, list):
                urls.extend(image_urls(message.content))
        precheck_data_uris(
            urls,
            max_single=self.config.multimodal_max_image_bytes,
            max_total=self.config.multimodal_max_add_bytes,
        )
        messages: list[Message] = []
        combined = SourceEnvelope([])
        for message in request.messages:
            if not message.role:
                raise ValueError("role is required")
            raw = message.envelope.parts if message.envelope else message.content
            envelope = message.envelope or normalize_content(raw)  # type: ignore[arg-type]
            combined.parts.extend(envelope.parts)
            text = envelope.text_for_extraction()
            if not text.strip() and not envelope.has_media():
                raise ValueError("message must include text or image content")
            messages.append(
                Message(
                    message.role,
                    text if text.strip() else "[image-only message]",
                    message.timestamp,
                    envelope=envelope,
                )
            )
        validate_media_limits(
            combined,
            max_single=self.config.multimodal_max_image_bytes,
            max_total=self.config.multimodal_max_add_bytes,
        )
        return AddRequest(request.request_id, request.user_id, request.session_id, messages)

    def extend_ingest(self, sources: list[Source], known, providers=None) -> IngestExtension:
        return ingest_sources(sources, providers, config=self.config)

    def extend_plan(self, plan: QueryPlan, query: str | list) -> QueryPlan:
        return plan_obligations(plan, query)

    def extend_retrieval(self, view: View, request: SearchRequest, plan: QueryPlan) -> SearchExtension:
        self._search_state = run_multimodal_search(view, request, self.config.candidate_limit, self.config)
        state = self._search_state
        channels, names = [], []
        if state.lexical_hits:
            channels.append(state.lexical_hits)
            names.append("multimodal_lexical")
        if state.query_image_hits:
            channels.append(state.query_image_hits)
            names.append("multimodal_query_image")
        return SearchExtension(extra_channels=channels, channel_names=names, trace=state.trace)

    def extend_evidence(self, view: View, request: SearchRequest, plan: QueryPlan,
                        evidence: list[Evidence]) -> tuple[list[Evidence], dict]:
        state = self._search_state or run_multimodal_search(view, request, self.config.candidate_limit, self.config)
        self._search_state = None
        enriched = apply_multimodal_evidence(view, state, evidence, self.config, request)
        return enriched, {"multimodal": state.trace}
