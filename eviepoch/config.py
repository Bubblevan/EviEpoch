from __future__ import annotations

import os
import math
from dataclasses import dataclass
from urllib.parse import urlsplit


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    db_path: str = "./data/eviepoch-v1.sqlite3"
    database_url: str = ""
    profile: str = "full"
    model_policy: str = "competition"
    selector: str = "beam"
    candidate_limit: int = 120
    graph_depth: int = 3
    graph_budget: int = 512
    beam_width: int = 12
    gap_iterations: int = 1
    relations_enabled: bool = True
    consolidation_enabled: bool = True
    reflection_enabled: bool = True
    proof_binding_enabled: bool = True
    workbench_enabled: bool = False
    rerank_url: str = ""
    add_concurrency: int = 16
    search_concurrency: int = 32
    extraction_chars: int = 24000
    extraction_split_depth: int = 4
    extraction_max_calls: int = 128
    extraction_deadline_seconds: float = 300.0
    extraction_input_tokens: int = 24000
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-4o-mini"
    embed_api_key: str = ""
    embed_base_url: str = ""
    embed_model: str = "text-embedding-v4"
    embed_dimensions: int = 1024
    auth_scheme: str = "bearer"
    auth_token: str = ""
    extract_facts: bool = True
    raw_ingest_fallback: bool = False
    recall_query: bool = True
    recall_weight: float = 0.5
    return_limit: int = 100
    char_budget: int = 400_000
    window_radius: int = 1
    raw_first: bool = True
    answer_input_tokens: int = 117_760
    answer_prompt_tokens: int = 1024
    answer_item_tokens: int = 20
    token_safety: float = 1.15
    request_timeout: float = 40.0
    max_facts: int = 24
    search_consistency_retries: int = 2
    temporal_anchors_enabled: bool = True
    structured_ingest_enabled: bool = True
    state_projection_enabled: bool = True
    coverage_enabled: bool = True
    episode_navigation_enabled: bool = True
    disclosure_enabled: bool = True
    binder_window_chars: int = 3200
    coverage_source_limit: int = 32

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("EPOCH_DB_PATH", "./data/eviepoch-v1.sqlite3"),
            database_url=os.getenv("EPOCH_DATABASE_URL", ""),
            profile=os.getenv("EPOCH_PROFILE", "full"),
            model_policy=os.getenv("EPOCH_MODEL_POLICY", "competition"),
            selector=os.getenv("EPOCH_SELECTOR", "beam"),
            candidate_limit=_int("EPOCH_CANDIDATE_LIMIT", 120),
            graph_depth=_int("EPOCH_GRAPH_DEPTH", 3),
            graph_budget=_int("EPOCH_GRAPH_BUDGET", 512),
            beam_width=_int("EPOCH_BEAM_WIDTH", 12),
            gap_iterations=_int("EPOCH_GAP_ITERATIONS", 1),
            relations_enabled=_bool("EPOCH_RELATIONS", True),
            consolidation_enabled=_bool("EPOCH_CONSOLIDATION", True),
            reflection_enabled=_bool("EPOCH_REFLECTION", True),
            proof_binding_enabled=_bool("EPOCH_PROOF_BINDING", True),
            workbench_enabled=_bool("EPOCH_WORKBENCH", False),
            rerank_url=os.getenv("EPOCH_RERANK_URL", ""),
            add_concurrency=_int("EPOCH_ADD_CONCURRENCY", 16),
            search_concurrency=_int("EPOCH_SEARCH_CONCURRENCY", 32),
            extraction_chars=_int("EPOCH_EXTRACTION_CHARS", 24000),
            extraction_split_depth=_int("EPOCH_EXTRACTION_SPLIT_DEPTH", 4),
            extraction_max_calls=_int("EPOCH_EXTRACTION_MAX_CALLS", 128),
            extraction_deadline_seconds=_float("EPOCH_EXTRACTION_DEADLINE_SECONDS", 300.0),
            extraction_input_tokens=_int("EPOCH_EXTRACTION_INPUT_TOKENS", 24000),
            llm_api_key=os.getenv("OPENAI_API_KEY", ""),
            llm_base_url=os.getenv("EPOCH_LLM_BASE_URL", ""),
            llm_model=os.getenv("EPOCH_LLM_MODEL", "gpt-4o-mini"),
            embed_api_key=os.getenv("EPOCH_EMBED_API_KEY", ""),
            embed_base_url=os.getenv("EPOCH_EMBED_BASE_URL", ""),
            embed_model=os.getenv("EPOCH_EMBED_MODEL", "text-embedding-v4"),
            embed_dimensions=_int("EPOCH_EMBED_DIMENSIONS", 1024),
            auth_scheme=os.getenv("EPOCH_AUTH_SCHEME", "bearer").strip().lower(),
            auth_token=os.getenv("EPOCH_AUTH_TOKEN", ""),
            extract_facts=_bool("EPOCH_EXTRACT_FACTS", True),
            raw_ingest_fallback=_bool("EPOCH_RAW_INGEST_FALLBACK", False),
            recall_query=_bool("EPOCH_RECALL_QUERY", True),
            recall_weight=_float("EPOCH_RECALL_WEIGHT", 0.5),
            return_limit=_int("EPOCH_RETURN_LIMIT", 100),
            char_budget=_int("EPOCH_CHAR_BUDGET", 400_000),
            window_radius=_int("EPOCH_WINDOW_RADIUS", 1),
            raw_first=_bool("EPOCH_RAW_FIRST", True),
            answer_input_tokens=_int("EPOCH_ANSWER_INPUT_TOKENS", 117_760),
            answer_prompt_tokens=_int("EPOCH_ANSWER_PROMPT_TOKENS", 1024),
            answer_item_tokens=_int("EPOCH_ANSWER_ITEM_TOKENS", 20),
            token_safety=_float("EPOCH_TOKEN_SAFETY", 1.15),
            request_timeout=_float("EPOCH_REQUEST_TIMEOUT", 40.0),
            max_facts=_int("EPOCH_MAX_FACTS", 24),
            search_consistency_retries=_int("EPOCH_SEARCH_CONSISTENCY_RETRIES", 2),
            temporal_anchors_enabled=_bool("EPOCH_TEMPORAL_ANCHORS", True),
            structured_ingest_enabled=_bool("EPOCH_STRUCTURED_INGEST", True),
            state_projection_enabled=_bool("EPOCH_STATE_PROJECTION", True),
            coverage_enabled=_bool("EPOCH_COVERAGE", True),
            episode_navigation_enabled=_bool("EPOCH_EPISODE_NAVIGATION", True),
            disclosure_enabled=_bool("EPOCH_DISCLOSURE", True),
            binder_window_chars=_int("EPOCH_BINDER_WINDOW_CHARS", 3200),
            coverage_source_limit=_int("EPOCH_COVERAGE_SOURCE_LIMIT", 32),
        )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    def validate(self) -> None:
        if (not 0 <= self.search_consistency_retries <= 4 or not 256 <= self.binder_window_chars <= 16000
                or not 1 <= self.coverage_source_limit <= 128):
            raise ValueError("Invalid consistency, window or coverage budget")
        if self.profile not in {"full", "hybrid", "b0"} or self.selector not in {"beam", "greedy", "topk"}:
            raise ValueError("Unknown profile or selector")
        if min(self.candidate_limit, self.graph_budget, self.beam_width, self.add_concurrency, self.search_concurrency) <= 0:
            raise ValueError("Candidate, graph, beam and concurrency budgets must be positive")
        if not 0 <= self.graph_depth <= 6 or not 0 <= self.gap_iterations <= 1 or self.extraction_chars < 256:
            raise ValueError("Invalid graph, gap or extraction budget")
        if (not 0 <= self.extraction_split_depth <= 8 or self.extraction_max_calls <= 0
                or self.extraction_input_tokens < 2048
                or not math.isfinite(self.extraction_deadline_seconds) or self.extraction_deadline_seconds <= 0):
            raise ValueError("Invalid bounded extraction retry policy")
        if self.model_policy not in {"competition", "research"}:
            raise ValueError("EPOCH_MODEL_POLICY must be competition or research")
        if self.profile == "full":
            if not self.llm_enabled or not self.extract_facts:
                raise ValueError("The full profile requires structured extraction and a configured LLM")
            if self.model_policy == "competition" and (self.llm_model != "gpt-4o-mini" or self.embed_model != "text-embedding-v4"):
                raise ValueError("Competition full profile requires gpt-4o-mini and text-embedding-v4")
        if self.auth_scheme not in {"none", "bearer", "token", "x-api-key"}:
            raise ValueError("EPOCH_AUTH_SCHEME must be none, bearer, token, or x-api-key")
        if self.auth_scheme != "none" and not self.auth_token:
            raise ValueError("Set EPOCH_AUTH_TOKEN or explicitly disable auth for local work")
        if not math.isfinite(self.recall_weight) or not 0 <= self.recall_weight <= 1:
            raise ValueError("EPOCH_RECALL_WEIGHT must be between 0 and 1")
        if self.embed_dimensions <= 0 or self.return_limit < 0 or self.window_radius < 0:
            raise ValueError("Embedding dimensions and retrieval limits are invalid")
        if self.max_facts < 0 or self.answer_input_tokens < 0 or self.answer_prompt_tokens < 0:
            raise ValueError("Fact and token limits must be non-negative")
        if (self.answer_item_tokens < 0 or not math.isfinite(self.token_safety)
                or self.token_safety < 1 or not math.isfinite(self.request_timeout)
                or self.request_timeout <= 0):
            raise ValueError("Token safety, item cost, or request timeout is invalid")
        if not self.embed_api_key or not self.embed_base_url:
            raise ValueError("Set EPOCH_EMBED_API_KEY and EPOCH_EMBED_BASE_URL")
        parts = urlsplit(self.embed_base_url)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("EPOCH_EMBED_BASE_URL must be HTTP(S) without credentials, query, or fragment")
        if self.llm_base_url:
            parts = urlsplit(self.llm_base_url)
            if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment):
                raise ValueError("EPOCH_LLM_BASE_URL must be HTTP(S) without credentials, query, or fragment")
