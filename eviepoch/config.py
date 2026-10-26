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
    db_path: str = "./data/eviepoch.sqlite3"
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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("EPOCH_DB_PATH", "./data/eviepoch.sqlite3"),
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
        )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    def validate(self) -> None:
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
