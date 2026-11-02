"""Structured visual observations via competition LLM (TRD §20.3, ATM field separation)."""
from __future__ import annotations

import json
from typing import Any

SGM_PROMPT = """You extract structured visual memory fields from one image for a personal memory system.

Return JSON only:
{
  "scene": {"value": "...", "origin": "VLM_observation"},
  "ocr": {"value": "...", "origin": "OCR"},
  "entities": [{"name": "...", "attributes": ["..."], "origin": "VLM_observation"}],
  "colors": [{"name": "...", "region": "...", "origin": "VLM_observation"}],
  "spatial": [{"relation": "...", "origin": "VLM_observation"}],
  "tags": [{"value": "...", "origin": "VLM_observation"}]
}

Rules:
1. Use empty strings or empty arrays when unknown; do not invent text not visible in the image.
2. Keep OCR separate from scene description; OCR is literal visible text only.
3. If adjacent user text is provided, treat it as supplied_text hints only — do not merge into a single authoritative caption.
4. Each field keeps its origin marker exactly as shown above or supplied_text when quoting user text."""


def default_observation(*, user_text: str = "") -> dict[str, Any]:
    fields: dict[str, Any] = {
        "scene": {"value": "", "origin": "VLM_observation"},
        "ocr": {"value": "", "origin": "OCR"},
        "entities": [],
        "colors": [],
        "spatial": [],
        "tags": [],
    }
    if user_text.strip():
        fields["supplied_text"] = {"value": user_text.strip(), "origin": "supplied_text"}
    return fields


def observation_index_body(fields: dict[str, Any]) -> str:
    chunks = []
    for key in ("scene", "ocr", "supplied_text"):
        block = fields.get(key) or {}
        value = block.get("value") if isinstance(block, dict) else ""
        if isinstance(value, str) and value.strip():
            chunks.append(f"{key}: {value.strip()}")
    for entity in fields.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        name = entity.get("name", "")
        attrs = entity.get("attributes") or []
        if name:
            chunks.append(f"entity {name} {' '.join(str(a) for a in attrs)}")
    for color in fields.get("colors") or []:
        if isinstance(color, dict) and color.get("name"):
            chunks.append(f"color {color.get('name')} {color.get('region', '')}".strip())
    for tag in fields.get("tags") or []:
        if isinstance(tag, dict) and tag.get("value"):
            chunks.append(f"tag {tag['value']}")
    for rel in fields.get("spatial") or []:
        if isinstance(rel, dict) and rel.get("relation"):
            chunks.append(f"spatial {rel['relation']}")
    return "\n".join(chunks)


def parse_observation_response(raw: Any, *, user_text: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_observation(user_text=user_text)
    merged = default_observation(user_text=user_text)
    for key in ("scene", "ocr"):
        if isinstance(raw.get(key), dict):
            merged[key] = raw[key]
    for key in ("entities", "colors", "spatial", "tags"):
        if isinstance(raw.get(key), list):
            merged[key] = raw[key]
    if user_text.strip():
        merged["supplied_text"] = {"value": user_text.strip(), "origin": "supplied_text"}
    return merged


def extract_observation(providers, data_uri: str, *, adjacent_text: str = "") -> dict[str, Any]:
    if providers is None or not getattr(providers, "visual_observation", None):
        return default_observation(user_text=adjacent_text)
    try:
        raw = providers.visual_observation(data_uri, adjacent_text)
        if isinstance(raw, str):
            raw = json.loads(raw)
        return parse_observation_response(raw, user_text=adjacent_text)
    except Exception:
        return default_observation(user_text=adjacent_text)
