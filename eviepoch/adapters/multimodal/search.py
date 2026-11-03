"""Multimodal retrieval channels and evidence hydration (TRD §20.5)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...config import Settings
from ...domain import Obligation, QueryPlan
from ...domain.content import ContentPart, data_uri_from_bytes, decode_data_uri, query_text
from ...retrieval.evidence import Evidence
from ...retrieval.lexical import terms
from ...storage.view import View
from ...governance.multimodal_closure import original_blocked_by_redaction
from .budget import MediaBudget
from .media_store import read_asset

_COLOR = re.compile(
    r"\b(?:red|blue|green|yellow|black|white|gray|grey|orange|purple|pink|brown|color|colour)\b|"
    r"颜色|什么颜色|红|蓝|绿|黄|黑|白|灰|橙|紫|粉|棕",
    re.I,
)
_POSITION = re.compile(r"位置|布局|左边|右边|上面|下面|哪里|where|left|right|above|below|position|layout", re.I)
_OCR = re.compile(
    r"图中文字|图中的字|图里的字|招牌|编号|\bocr\b|"
    r"text (?:is )?in the (?:image|picture|photo)",
    re.I,
)
_CHANGE = re.compile(
    r"变化|改变|变了|前后|之前|之后|以前|对比|比较|"
    r"\b(?:change[ds]?|before|after|compare|comparison|used to)\b",
    re.I,
)


def visual_obligations(query: str | list[ContentPart]) -> list[str]:
    text = query_text(query)
    found = []
    if _COLOR.search(text):
        found.append("color")
    if _POSITION.search(text):
        found.append("position")
    if _OCR.search(text):
        found.append("ocr")
    if _CHANGE.search(text):
        found.append("change")
    return found


def build_query_bundle(query: str | list[ContentPart]) -> dict[str, Any]:
    """Request-only bundle. Image hashes come from decoded bytes and are not stored."""
    if isinstance(query, str):
        return {"parts": ["text"], "image_hashes": [], "visual_obligations": visual_obligations(query)}
    parts = []
    hashes = []
    for item in query:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        parts.append(kind)
        if kind != "image_url":
            continue
        url = (item.get("image_url") or {}).get("url") or ""
        _mime, raw = decode_data_uri(url)
        hashes.append(hashlib.sha256(raw).hexdigest())
    return {"parts": parts, "image_hashes": hashes, "visual_obligations": visual_obligations(query)}


def plan_obligations(plan: QueryPlan, query: str | list[ContentPart]) -> QueryPlan:
    text = query_text(query)
    intents = list(dict.fromkeys(plan.intents + ["multimodal"]))
    obligations = list(plan.obligations)
    for name in visual_obligations(query):
        obligations.append(Obligation(f"mm-visual-{name}", f"{name}: {text}"))
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


def pack_multimodal_content(view: View, source_id: str, config: Settings, *,
                            include_images: bool = True, budget: MediaBudget | None = None) -> list[ContentPart] | str:
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
            text = prefix + part.get("text", "")
            out.append({"type": "text", "text": text})
            if budget is not None:
                budget.note_text(text)
            prefix = ""
            continue
        if part.get("type") != "image_url" or not include_images or budget is None:
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
        if original_blocked_by_redaction(_connection(view), view.ns, asset_id):
            budget.omitted.append({"reason": "redaction_pending", "content_hash": asset.get("content_hash")})
            continue
        url = budget.allow_image(read_asset(path), asset["mime"], asset)
        if not url:
            continue
        out.append({"type": "image_url", "image_url": {"url": url}})
    return out if out else source.content


def hydrate_multimodal_evidence(view: View, evidence: list[Evidence], config: Settings, *,
                                include_images: bool = True, budget: MediaBudget | None = None) -> list[Evidence]:
    if budget is None:
        budget = MediaBudget(config, "", None)
    enriched = []
    for item in evidence:
        if item.source_id != item.id and item.id != item.source_id:
            enriched.append(item)
            continue
        packed = pack_multimodal_content(
            view, item.source_id, config, include_images=include_images, budget=budget,
        )
        if isinstance(packed, list):
            enriched.append(replace(item, content=packed))
        else:
            enriched.append(item)
    return enriched


def returned_image_hashes(view: View, evidence: list[Evidence]) -> tuple[list[str], bool]:
    """Hash bytes actually packed into the response and compare them with the asset row."""
    found = []
    matches = []
    for item in evidence:
        content = item.content
        if not isinstance(content, list):
            continue
        asset_hashes = []
        for part in _source_envelope(view, item.source_id):
            asset_id = part.get("asset_id")
            if part.get("type") != "image_url" or not asset_id:
                continue
            asset = _load_asset_row(view, asset_id)
            if asset and asset.get("content_hash"):
                asset_hashes.append(asset["content_hash"])
        for part in content:
            if part.get("type") != "image_url":
                continue
            _mime, raw = decode_data_uri(part["image_url"]["url"])
            digest = hashlib.sha256(raw).hexdigest()
            found.append(digest)
            matches.append(digest in asset_hashes)
    if not found:
        return [], False
    return found, all(matches)


def query_image_channel(view: View, query: str | list[ContentPart], limit: int) -> dict[str, float]:
    """Visual embedding recall stays off. A caption overlap is not an image match."""
    _ = (view, query, limit)
    return {}
