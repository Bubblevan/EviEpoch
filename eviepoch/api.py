from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .models import AddRequest, Message, SearchRequest
from .pipeline import MemoryPipeline
from .providers import ProviderUnavailable


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str
    timestamp: int | None = None


class AddBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str
    user_id: str
    session_id: str
    messages: list[MessageBody] = Field(min_length=1)


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: str
    user_id: str
    top_k: int
    options: list[str] | None = None


class AddResponse(BaseModel):
    success: bool
    request_id: str
    user_id: str
    session_id: str


settings = Settings.from_env()
pipeline = MemoryPipeline(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate()
    pipeline.start()
    try:
        yield
    finally:
        pipeline.close()


app = FastAPI(title="EviEpoch", version="0.1.0", lifespan=lifespan)


def _authorize(authorization: str | None, x_api_key: str | None) -> None:
    if settings.auth_scheme == "none":
        return
    supplied: list[str] = []
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in {"bearer", "token"}:
            supplied.append(parts[1].strip())
        else:
            supplied.append(authorization.strip())
    if x_api_key:
        supplied.append(x_api_key.strip())
    if settings.auth_token and any(hmac.compare_digest(settings.auth_token, value) for value in supplied):
        return
    raise HTTPException(status_code=401, detail={"reason": "invalid credentials"})


@app.exception_handler(ProviderUnavailable)
async def provider_unavailable(_request: Any, _exc: ProviderUnavailable) -> JSONResponse:
    return JSONResponse(
        {"detail": {"reason": "embedding service unavailable"}},
        status_code=503,
        headers={"Retry-After": "2"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/add", response_model=AddResponse)
def add(
    body: AddBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    _authorize(authorization, x_api_key)
    request = AddRequest(
        request_id=body.request_id,
        user_id=body.user_id,
        session_id=body.session_id,
        messages=[Message(role=m.role, content=m.content, timestamp=m.timestamp) for m in body.messages],
    )
    return pipeline.add(request)


@app.post("/search")
def search(
    body: SearchBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    _authorize(authorization, x_api_key)
    return pipeline.search(SearchRequest(
        query=body.query,
        user_id=body.user_id,
        top_k=body.top_k,
        options=body.options,
    ))
