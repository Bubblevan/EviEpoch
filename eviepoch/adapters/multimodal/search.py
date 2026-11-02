"""Multimodal retrieval channels and evidence hydration (TRD §20.5)."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...config import Settings
from ...domain import Obligation, QueryPlan
from ...domain.content import ContentPart, data_uri_from_bytes, query_text
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...retrieval.lexical import terms
from ...storage.view import View
from .media_store import read_asset
_COLOR = re.compile(r"\b(?:red|blue|green|yellow|black|white|gray|grey|orange|purple|pink|brown)\b|红|蓝|绿|黄|黑|白|灰|橙|紫|粉|棕", re.I)
_OCR_NUM = re.compile(r"\b\d{2,}\b|[A-Z0-9]{4,}")


def plan_obligations(plan: QueryPlan, query: str | list[ContentPart]) -> QueryPlan:
    text = query_text(query)
    intents = list(dict.fromkeys(plan.intents + ["multimodal"]))
    labels = ["dialogue_context", "visual_detail", "ocr_or_text_in_image"]
    if _COLOR.search(text):
        labels.append("color_or_layout")
    if isinstance(query, list) and any(p.get("type") == "image_url" for p in query):
        labels.append("query_image_match")
    obligations = list(plan.obligations)
    for index, label in enumerate(labels[:6], start=1):
        obligations.append(Obligation(f"mm-{label}-{index}", f"{label}: {text}"))
    return replace(plan, intents=intents, obligations=obligations)


def _connection(view: View):
    return view.repo._conn if hasattr(view.repo, "_conn") else view.connection


def multimodal_lexical_channel(view: View, query: str | list[ContentPart], limit: int) -> dict[str, float]:
    text = query_text(query)
    wanted = set(terms(text))
    if not wanted:
        return {}
    scores: dict[str, float] = {}
    placeholders = ",".join("?" for _ in wanted)
    rows = _connection(view).execute(
        f"SELECT p.object_id,SUM(p.tf) AS tf,o.generation FROM ee_postings p "
        f"JOIN ee_objects o ON o.ns=p.ns AND o.id=p.object_id "
        f"WHERE p.ns=? AND o.active>0 AND o.kind LIKE 'multimodal_%' AND p.term IN ({placeholders}) "
        f"GROUP BY p.object_id,o.generation ORDER BY tf DESC,o.generation DESC LIMIT ?",
        (view.ns, *wanted, limit),
    ).fetchall()
    for rank, (oid, tf, generation) in enumerate(rows):
        scores[oid] = max(scores.get(oid, 0.0), float(tf) / (60 + rank) + generation * 1e-6)
    return scores


def promote_multimodal_hits(view: View, scores: dict[str, float]) -> dict[str, float]:
    promoted: dict[str, float] = {}
    for oid, score in scores.items():
        row = _connection(view).execute(
            "SELECT kind FROM ee_objects WHERE ns=? AND id=? AND active>0",
            (view.ns, oid),
        ).fetchone()
        if not row:
            continue
        kind = str(row[0])
        if kind.startswith("multimodal_"):
            for (sid,) in _connection(view).execute(
                "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?",
                (view.ns, oid),
            ).fetchall():
                promoted[sid] = max(promoted.get(sid, 0.0), score)
        else:
            promoted[oid] = max(promoted.get(oid, 0.0), score)
    return promoted


def _load_asset_row(view: View, asset_id: str) -> dict[str, Any] | None:
    row = _connection(view).execute(
        "SELECT payload,storage_path,mime,byte_length,content_hash FROM ee_media_assets WHERE ns=? AND asset_id=?",
        (view.ns, asset_id),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row[0]) if row[0] else {}
    payload.update(
        {"storage_path": row[1], "mime": row[2], "byte_length": row[3], "content_hash": row[4], "asset_id": asset_id}
    )
    return payload


def _source_envelope(view: View, source_id: str) -> list[dict[str, Any]]:
    row = _connection(view).execute(
        "SELECT payload FROM ee_objects WHERE ns=? AND id=? AND kind='source'",
        (view.ns, source_id),
    ).fetchone()
    if not row:
        return []
    data = json.loads(row[0])
    locator = data.get("locator") or {}
    envelope = locator.get("content_envelope") or {}
    return envelope.get("parts") or []


def pack_multimodal_content(view: View, source_id: str, config: Settings) -> list[ContentPart] | str:
    snap = view.snapshot({source_id})
    source = snap.sources.get(source_id)
    if not source:
        return ""
    locator = source.locator or {}
    envelope = locator.get("content_envelope") or {}
    parts_meta = envelope.get("parts") or []
    if not parts_meta:
        return source.content
    out: list[ContentPart] = []
    label_time = source.asserted_at
    role = source.role
    prefix = f"[{label_time}][{role}] " if label_time else f"[{role}] "
    for part in sorted(parts_meta, key=lambda p: p.get("part_ordinal", 0)):
        if part.get("type") == "text":
            out.append({"type": "text", "text": prefix + part.get("text", "")})
            prefix = ""
            continue
        if part.get("type") != "image_url":
            continue
        asset_id = part.get("asset_id")
        if not asset_id:
            continue
        asset = _load_asset_row(view, asset_id)
        if not asset:
            continue
        path = Path(asset["storage_path"])
        if not path.is_file():
            continue
        raw = read_asset(path)
        if len(raw) > config.multimodal_max_image_bytes:
            continue
        out.append({"type": "image_url", "image_url": {"url": data_uri_from_bytes(raw, asset["mime"])}})
    return out if out else source.content


def hydrate_multimodal_evidence(view: View, evidence: list[Evidence], config: Settings) -> list[Evidence]:
    enriched = []
    for item in evidence:
        if item.source_id != item.id and item.id != item.source_id:
            enriched.append(item)
            continue
        packed = pack_multimodal_content(view, item.source_id, config)
        if isinstance(packed, list):
            enriched.append(replace(item, content=packed))
        else:
            enriched.append(item)
    return enriched


def query_image_channel(view: View, query: str | list[ContentPart], limit: int) -> dict[str, float]:
    """Competition path: OCR/color token overlap boosted when query carries an image (caption-free)."""
    if not isinstance(query, list):
        return {}
    text = query_text(query)
    scores = multimodal_lexical_channel(view, text or "image", limit)
    if _OCR_NUM.search(text):
        for oid in list(scores):
            if "observation" in oid:
                scores[oid] *= 1.1
    return scores
