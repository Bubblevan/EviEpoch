"""Official Add/Search contract and an explicitly opt-in public-data workbench."""
from __future__ import annotations

import hmac
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings
from .audit import PayloadAuditMiddleware
from ..observability.diagnostics import log_api_failure, request_context
from ..domain import IdempotencyConflict, IngestBusy, InvalidExtraction
from ..domain.requests import AddRequest, Message, SearchRequest
from ..pipeline import MemoryPipeline
from ..providers import ProviderUnavailable


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)
    timestamp: int | None = None

    @field_validator("role", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class AddBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    messages: list[MessageBody] = Field(min_length=1)


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    top_k: int = Field(ge=0)
    options: list[str] | None = None


class AddResponse(BaseModel):
    success: bool
    request_id: str
    user_id: str
    session_id: str


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def create_app(config: Settings | None = None, memory=None) -> FastAPI:
    config = config or Settings.from_env()
    memory = memory or MemoryPipeline(config)

    @asynccontextmanager
    async def lifespan(_app):
        config.validate()
        memory.start()
        try:
            yield
        finally:
            memory.close()

    app = FastAPI(title="EviEpoch", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        PayloadAuditMiddleware,
        audit_dir=os.getenv("EPOCH_AUDIT_DIR", ""),
        auth_token=config.auth_token,
        max_body_bytes=_env_positive_int("EPOCH_AUDIT_MAX_BODY_BYTES", 1_048_576),
        max_disk_bytes=_env_positive_int("EPOCH_AUDIT_MAX_DISK_BYTES", 2_147_483_648),
        retention_days=_env_positive_int("EPOCH_AUDIT_RETENTION_DAYS", 30),
    )
    app.state.pipeline = memory

    def authorize(authorization, x_api_key):
        if config.auth_scheme == "none":
            return
        supplied = []
        if authorization:
            parts = authorization.split(None, 1)
            if len(parts) == 2 and parts[0].lower() in {"bearer", "token"}:
                supplied.append(parts[1].strip())
        if x_api_key:
            supplied.append(x_api_key.strip())
        if config.auth_token and any(hmac.compare_digest(config.auth_token.encode(), token.encode()) for token in supplied):
            return
        raise HTTPException(status_code=401, detail={"reason": "invalid credentials"})

    @app.exception_handler(IdempotencyConflict)
    async def conflict(_request, _error):
        return JSONResponse({"detail": {"reason": "request_id payload conflict"}}, status_code=409)

    @app.exception_handler(IngestBusy)
    async def busy(_request, _error):
        return JSONResponse({"detail": {"reason": "capacity or in-flight request; retry identical payload"}},
                            status_code=429, headers={"Retry-After": "2"})

    @app.exception_handler(ProviderUnavailable)
    @app.exception_handler(InvalidExtraction)
    async def unavailable(_request, _error):
        return JSONResponse({"detail": {"reason": "required model stage unavailable or source validation failed"}},
                            status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(ValueError)
    async def invalid(_request, _error):
        return JSONResponse({"detail": {"reason": "invalid request or source timestamp"}}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        if hasattr(memory.store, "view"):
            with memory.store.view(""):
                pass
        else:
            memory.store.stats()
        return {"status": "ready", "profile": config.profile, "schema": 1, "model_policy": config.model_policy}

    @app.get("/metrics")
    def metrics(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)):
        authorize(authorization, x_api_key)
        return memory.metrics.snapshot() if hasattr(memory, "metrics") else {"profile": "b0"}

    @app.post("/add", response_model=AddResponse)
    def add(body: AddBody, authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None)):
        authorize(authorization, x_api_key)
        started = time.perf_counter()
        input_chars = sum(len(message.content) for message in body.messages)
        with request_context(operation="add", request_id=body.request_id,
                             fingerprint_key=config.auth_token, message_count=len(body.messages),
                             input_chars=input_chars):
            try:
                return memory.add(AddRequest(body.request_id, body.user_id, body.session_id,
                                             [Message(m.role, m.content, m.timestamp) for m in body.messages]))
            except (IngestBusy, ProviderUnavailable, InvalidExtraction) as exc:
                log_api_failure(
                    operation="add",
                    error=exc,
                    status_code=429 if isinstance(exc, IngestBusy) else 503,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    request_id=body.request_id,
                    fingerprint_key=config.auth_token,
                    message_count=len(body.messages),
                    input_chars=input_chars,
                )
                raise

    @app.post("/search")
    def search(body: SearchBody, authorization: str | None = Header(default=None),
               x_api_key: str | None = Header(default=None)):
        authorize(authorization, x_api_key)
        started = time.perf_counter()
        with request_context(operation="search", query_chars=len(body.query), top_k=body.top_k,
                             options_count=len(body.options or [])):
            try:
                return memory.search(SearchRequest(body.query, body.user_id, body.top_k, body.options))
            except (IngestBusy, ProviderUnavailable, InvalidExtraction) as exc:
                log_api_failure(
                    operation="search",
                    error=exc,
                    status_code=429 if isinstance(exc, IngestBusy) else 503,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    fingerprint_key=config.auth_token,
                    query_chars=len(body.query),
                    top_k=body.top_k,
                    options_count=len(body.options or []),
                )
                raise

    if config.workbench_enabled and config.profile != "b0":
        from pathlib import Path

        @app.get("/workbench", response_class=HTMLResponse)
        def workbench():
            return Path(__file__).with_name("workbench.html").read_text(encoding="utf-8")

        @app.get("/research/memory")
        def inspect(user_id: str, authorization: str | None = Header(default=None),
                    x_api_key: str | None = Header(default=None)):
            authorize(authorization, x_api_key)
            return memory.inspect(user_id)

        @app.post("/research/search")
        def trace(body: SearchBody, authorization: str | None = Header(default=None),
                  x_api_key: str | None = Header(default=None)):
            authorize(authorization, x_api_key)
            data, details = memory.search_with_trace(SearchRequest(body.query, body.user_id, body.top_k, body.options))
            return {"response": data, "trace": details}

    return app


app = create_app()
