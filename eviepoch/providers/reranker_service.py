"""Standalone HTTP service for the local Qwen3 cross-encoder."""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MODEL = None
_MODEL_LOCK = threading.Lock()


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=32_000)
    segments: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def has_text(self):
        if not self.segments and self.text is None:
            raise ValueError("a document requires text or segments")
        if any(not isinstance(segment, str) or len(segment) > 32_000 for segment in self.segments):
            raise ValueError("document segments exceed the allowed size")
        return self

    def passages(self) -> list[str]:
        return self.segments or [self.text or ""]


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=32_000)
    documents: list[Document] = Field(min_length=1, max_length=256)

    @field_validator("documents")
    @classmethod
    def unique_ids(cls, documents):
        ids = [document.id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")
        return documents


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _MODEL
    model_path = Path(os.getenv("EPOCH_RERANK_MODEL_PATH", "./models/Qwen3-Reranker-0.6B"))
    if not (model_path / "config.json").is_file():
        raise RuntimeError(f"Qwen3 reranker model is missing at {model_path}")

    import torch
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    _MODEL = CrossEncoder(str(model_path), max_length=8192, device=device,
                          model_kwargs={"torch_dtype": dtype})
    yield
    _MODEL = None


app = FastAPI(title="EviEpoch Qwen3 Reranker", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ready" if _MODEL is not None else "loading"}


@app.post("/rerank")
def rerank(request: RerankRequest):
    if _MODEL is None:
        raise HTTPException(status_code=503, detail="reranker model is not ready")
    pairs, owners = [], []
    for owner, document in enumerate(request.documents):
        for passage in document.passages():
            pairs.append((request.query, passage))
            owners.append(owner)
    if not _MODEL_LOCK.acquire(timeout=10.0):
        raise HTTPException(status_code=503, detail="reranker is busy")
    try:
        scores = _MODEL.predict(
            pairs,
            batch_size=max(1, int(os.getenv("EPOCH_RERANK_BATCH_SIZE", "8"))),
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        scores = np.asarray(scores, dtype=np.float64).reshape(-1)
        if len(scores) != len(pairs) or not np.isfinite(scores).all():
            raise ValueError("model returned an invalid score vector")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="reranker inference failed") from exc
    finally:
        _MODEL_LOCK.release()
    best = [-np.inf] * len(request.documents)
    for owner, score in zip(owners, scores):
        best[owner] = max(best[owner], float(score))
    if not np.isfinite(best).all():
        raise HTTPException(status_code=503, detail="model omitted a document score")
    return {"scores": [{"id": document.id, "score": score}
                       for document, score in zip(request.documents, best)]}
