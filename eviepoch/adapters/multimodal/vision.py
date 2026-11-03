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


_ORIGINS = {"supplied_text", "supplied_caption", "exif", "OCR", "VLM_observation", "inferred_relation"}
_MODEL_ORIGINS = {"VLM_observation", "inferred_relation"}
_PROMPT_ID = "sgm-v1"


def _blank() -> dict[str, Any]:
    return {"value": "", "origin": None, "extracted": False}


def _user_field(parts: list[str], origin: str) -> dict[str, Any]:
    kept = [part for part in parts if isinstance(part, str) and part.strip()]
    if not kept:
        return _blank()
    return {"parts": kept, "value": kept[0] if len(kept) == 1 else "\n".join(kept), "origin": origin, "extracted": True}


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= float(value) <= 1:
        return None
    return float(value)


def _checked_text(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _blank()
    origin = raw.get("origin")
    text = raw.get("value") if isinstance(raw.get("value"), str) else ""
    if origin not in allowed:
        return _blank()
    return {"value": text, "origin": origin, "extracted": True, "confidence": _confidence(raw.get("confidence"))}


def _checked_items(raw: Any, value_key: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    kept = []
    for item in raw:
        if not isinstance(item, dict) or item.get("origin") not in _MODEL_ORIGINS:
            continue
        value = item.get(value_key)
        if not isinstance(value, str) or not value.strip():
            continue
        copied = {
            value_key: value,
            "origin": item["origin"],
            "confidence": _confidence(item.get("confidence")),
        }
        if value_key == "name":
            attributes = item.get("attributes") if isinstance(item.get("attributes"), list) else []
            copied["attributes"] = [str(attr) for attr in attributes if str(attr).strip()]
        if value_key == "name" and isinstance(item.get("region"), str):
            copied["region"] = item["region"]
        if value_key == "relation":
            pass
        kept.append(copied)
    return kept


def default_observation(*, user_parts: list[str] | None = None, exif_time: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "scene": _blank(),
        "ocr": _blank(),
        "entities": [],
        "colors": [],
        "spatial": [],
        "tags": [],
        "supplied_text": _user_field(user_parts or [], "supplied_text"),
        "supplied_caption": _blank(),
        "exif": _user_field([exif_time], "exif") if exif_time else _blank(),
    }
    if exif_time:
        fields["exif"] = {"value": exif_time, "origin": "exif", "extracted": True}
    return fields


def observation_index_body(fields: dict[str, Any]) -> str:
    chunks = []
    for key in ("scene", "ocr", "supplied_text", "supplied_caption", "exif"):
        block = fields.get(key) or {}
        if not isinstance(block, dict):
            continue
        parts = block.get("parts") if isinstance(block.get("parts"), list) else []
        values = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
        if not values and isinstance(block.get("value"), str) and block["value"].strip():
            values = [block["value"].strip()]
        chunks.extend(f"{key}: {value}" for value in values)
    for entity in fields.get("entities") or []:
        if isinstance(entity, dict) and entity.get("name"):
            attrs = " ".join(str(item) for item in entity.get("attributes") or [])
            chunks.append(f"entity {entity['name']} {attrs}".strip())
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


def parse_observation_response(raw: Any, *, user_parts: list[str] | None = None, exif_time: str | None = None) -> dict[str, Any]:
    parsed = default_observation(user_parts=user_parts, exif_time=exif_time)
    if not isinstance(raw, dict):
        return parsed
    parsed["scene"] = _checked_text(raw.get("scene"), _MODEL_ORIGINS)
    parsed["ocr"] = _checked_text(raw.get("ocr"), {"OCR"})
    parsed["entities"] = _checked_items(raw.get("entities"), "name")
    parsed["colors"] = _checked_items(raw.get("colors"), "name")
    for color in parsed["colors"]:
        raw_colors = raw.get("colors") if isinstance(raw.get("colors"), list) else []
        matched = next((item for item in raw_colors if isinstance(item, dict) and item.get("name") == color["name"]), {})
        if isinstance(matched, dict) and isinstance(matched.get("region"), str):
            color["region"] = matched["region"]
    parsed["spatial"] = _checked_items(raw.get("spatial"), "relation")
    parsed["tags"] = _checked_items(raw.get("tags"), "value")
    parsed["supplied_text"] = _user_field(user_parts or [], "supplied_text")
    parsed["supplied_caption"] = _blank()
    parsed["exif"] = {"value": exif_time, "origin": "exif", "extracted": True} if exif_time else _blank()
    parsed.pop("caption", None)
    return parsed


def _representation(*, model: str | None, prompt_id: str | None, width, height, namespace: str, extracted: bool) -> dict[str, Any]:
    return {
        "view_kind": "visual_observation",
        "model": model,
        "prompt_id": prompt_id,
        "width": width,
        "height": height,
        "rotation": None,
        "schema_version": _PROMPT_ID,
        "namespace": namespace,
        "extracted": extracted,
    }


def _ocr_span(ocr: dict[str, Any], model: str | None) -> dict[str, Any] | None:
    text = ocr.get("value") if isinstance(ocr, dict) else ""
    if not isinstance(text, str) or not text.strip():
        return None
    return {
        "text": text,
        "language": None,
        "bbox": None,
        "polygon": None,
        "model": model,
        "confidence": ocr.get("confidence"),
        "origin": "OCR",
    }


def _package(fields: dict[str, Any], *, model, prompt_id, compared: bool, arm: str, width, height, namespace: str, extracted: bool) -> dict[str, Any]:
    fields.pop("caption", None)
    return {
        "fields": fields,
        "representation": _representation(
            model=model, prompt_id=prompt_id, width=width, height=height, namespace=namespace, extracted=extracted,
        ),
        "model": model,
        "prompt_id": prompt_id,
        "confidence": None,
        "compared_to_original": compared,
        "experiment_arm": arm,
        "ocr_span": _ocr_span(fields.get("ocr") or {}, model),
    }


def extract_observation(providers, data_uri: str, *, adjacent_texts: list[str] | None = None,
                        adjacent_text: str = "", exif_time: str | None = None, width=None, height=None,
                        namespace: str = "", caption_only: bool = False) -> dict[str, Any]:
    parts = list(adjacent_texts or [])
    if not parts and adjacent_text.strip():
        parts = [adjacent_text]
    if caption_only:
        fields = default_observation(user_parts=parts, exif_time=exif_time)
        fields["status"] = {"extracted": False, "vision": "not_called", "experiment_arm": "caption_only"}
        return _package(
            fields, model=None, prompt_id=None, compared=False, arm="caption_only",
            width=width, height=height, namespace=namespace, extracted=False,
        )
    runner = getattr(providers, "visual_observation", None) if providers is not None else None
    if not callable(runner):
        fields = default_observation(user_parts=parts, exif_time=exif_time)
        fields["status"] = {"extracted": False, "vision": "unavailable", "experiment_arm": "multimodal"}
        return _package(
            fields, model=None, prompt_id=None, compared=False, arm="multimodal",
            width=width, height=height, namespace=namespace, extracted=False,
        )
    try:
        raw = runner(data_uri, "\n".join(parts))
        if isinstance(raw, str):
            raw = json.loads(raw)
    except Exception as exc:
        raise ValueError("visual observation failed; multimodal add will not be recorded as caption-only") from exc
    if not isinstance(raw, dict):
        raise ValueError("visual observation failed; multimodal add will not be recorded as caption-only")
    fields = parse_observation_response(raw, user_parts=parts, exif_time=exif_time)
    fields["status"] = {"extracted": True, "vision": "completed", "experiment_arm": "multimodal"}
    model = getattr(providers, "visual_model_name", None)
    if not isinstance(model, str) or not model.strip():
        model = None
    return _package(
        fields, model=model, prompt_id=_PROMPT_ID, compared=True, arm="multimodal",
        width=width, height=height, namespace=namespace, extracted=True,
    )
