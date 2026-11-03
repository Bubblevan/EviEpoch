"""Change-question evidence set H/S (TRD §5 admissible)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ...domain import stable_id
from ...domain.content import decode_data_uri, query_text
from ...retrieval.evidence import Evidence
from ...retrieval.lexical import terms
from ...storage.view import View
from .search import _connection, _load_asset_row, promote_multimodal_hits

_SAME = re.compile(r"同一|同一个|还是那|还是这个|\bsame\b", re.I)
_DIFFERENT = re.compile(r"另一|不是同一个|不同的|\banother\b|\bdifferent\b", re.I)
_REPRESENTATION = "sgm-v1"


def is_change_question(query: str | list) -> bool:
    from .search import _CHANGE
    return bool(_CHANGE.search(query_text(query)))


def _rows(view: View, source_id: str) -> list[dict[str, Any]]:
    found = _connection(view).execute(
        "SELECT payload FROM ee_media_occurrences WHERE ns=? AND source_id=? ORDER BY part_ordinal",
        (view.ns, source_id),
    ).fetchall()
    return [json.loads(row[0]) for row in found]


def own_time(payload: dict[str, Any]) -> str | None:
    """A time that belongs to this occurrence. stored_at is not a substitute."""
    for key in ("message_time", "depicted_date", "exif_time", "captured_at"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _source_text(view: View, source_id: str) -> str:
    source = view.snapshot({source_id}).sources.get(source_id)
    return source.content if source else ""


def order_change_evidence(view: View, evidence: list[Evidence], lexical_hits: dict[str, float],
                          query: str | list) -> list[Evidence]:
    """Pull the recalled pair into one candidate list, earlier time first."""
    if not is_change_question(query):
        return evidence
    wanted = set(terms(query_text(query)))
    by_id: dict[str, Evidence] = {}
    others: list[Evidence] = []
    for item in evidence:
        if item.id == item.source_id and item.source_id:
            by_id.setdefault(item.source_id, item)
        else:
            others.append(item)
    for source_id, score in promote_multimodal_hits(view, lexical_hits).items():
        if source_id in by_id:
            continue
        text = _source_text(view, source_id)
        if wanted and not (wanted & set(terms(text))):
            continue
        by_id[source_id] = Evidence(source_id, text, None, score, source_id)

    def sort_key(item: Evidence) -> tuple:
        payloads = _rows(view, item.source_id)
        clocks = [own_time(payload) for payload in payloads]
        clock = next((value for value in clocks if value), None)
        return (clock is None, clock or "", item.source_id)

    ordered = sorted(by_id.values(), key=sort_key)
    return ordered + others


def _returned_hashes(content: str | list) -> list[str]:
    if not isinstance(content, list):
        return []
    found = []
    for part in content:
        if part.get("type") != "image_url":
            continue
        _mime, raw = decode_data_uri(part["image_url"]["url"])
        found.append(hashlib.sha256(raw).hexdigest())
    return found


def _user_text(content: str | list, fallback: str) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [str(part.get("text") or "") for part in content if part.get("type") == "text"]
        joined = "\n".join(text for text in texts if text.strip())
        if joined.strip():
            return joined
    return fallback


def assess_change(view: View, evidence: list[Evidence], query: str | list) -> dict[str, Any]:
    endpoints = []
    for item in evidence:
        if item.id != item.source_id or not item.source_id:
            continue
        returned = _returned_hashes(item.content)
        text = _user_text(item.content, _source_text(view, item.source_id))
        for payload in _rows(view, item.source_id):
            asset = _load_asset_row(view, payload.get("asset_id") or "")
            expected = asset.get("content_hash") if asset else None
            disclosable = bool(expected) and expected in returned
            if disclosable:
                returned.remove(expected)
            endpoints.append({
                "source_id": item.source_id,
                "content_hash": expected,
                "time_support": own_time(payload),
                "disclosable": disclosable,
                "text": text,
            })
    first = endpoints[0] if endpoints else None
    second = endpoints[1] if len(endpoints) > 1 else None
    image_t1 = _slot(first)
    image_t2 = _slot(second)
    time_t1 = first["time_support"] if first else None
    time_t2 = second["time_support"] if second else None
    both_images = bool(first and second and first["disclosable"] and second["disclosable"])
    both_times = bool(time_t1 and time_t2 and time_t1 != time_t2)
    texts = "\n".join(item["text"] for item in (first, second) if item)
    query_body = query_text(query)
    shared = sorted(set(terms(query_body)) & set(terms(texts)))
    explicit_same = bool(_SAME.search(query_body) or (first and _SAME.search(first["text"])) or (second and _SAME.search(second["text"])))
    explicit_different = bool(_DIFFERENT.search(texts) or _DIFFERENT.search(query_body))
    correspondence = bool(shared) and explicit_same and not explicit_different and both_images
    hashes = [item["content_hash"] for item in (first, second) if item and item.get("content_hash")]
    unresolved = (
        hashes
        if both_images and len(hashes) == 2 and hashes[0] != hashes[1] and not correspondence
        else []
    )
    collapsed = False
    gaps = []
    if first is None or not first["disclosable"]:
        gaps.append("image_at_t1")
    elif not time_t1:
        gaps.append("time_support_t1")
    if second is None or not second["disclosable"]:
        gaps.append("image_at_t2")
    elif not time_t2:
        gaps.append("time_support_t2")
    if time_t1 and time_t2 and time_t1 == time_t2:
        gaps.append("time_order")
    if unresolved:
        gaps.append("unresolved_identity")
    if both_images and not correspondence:
        gaps.append("entity_correspondence")
    admissible = (
        both_images
        and both_times
        and correspondence
        and not collapsed
    )
    source_order = []
    for item in endpoints:
        if item["source_id"] not in source_order:
            source_order.append(item["source_id"])
    bundle_id = stable_id(
        "mm-evidence-bundle-v1",
        _REPRESENTATION,
        source_order,
        [item["content_hash"] for item in endpoints],
    )
    return {
        "change_question": True,
        "H": {
            "entity_hypothesis": shared,
            "time_hypothesis": [value for value in (time_t1, time_t2) if value],
            "unresolved_identity_conflicts": unresolved,
        },
        "S": {
            "image_at_t1": image_t1,
            "time_support_t1": time_t1,
            "image_at_t2": image_t2,
            "time_support_t2": time_t2,
            "entity_correspondence": {"supported": correspondence, "shared_terms": shared},
            "attribute_comparison": {
                "supported": False,
                "reason": "retrieval does not judge the attribute",
            },
        },
        "checks": {
            "both_images_disclosable": both_images,
            "each_image_has_its_own_time_support": both_times,
            "entity_correspondence_is_supported": correspondence,
            "no_unresolved_identity_conflict_is_silently_collapsed": not collapsed,
        },
        "admissible": admissible,
        "comparison_complete": admissible,
        "collapsed_identity": collapsed,
        "single_image_is_not_a_comparison": len(endpoints) == 1,
        "gaps": gaps,
        "source_order": source_order,
        "bundle_id": bundle_id,
    }


def _slot(endpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    if endpoint is None:
        return None
    return {
        "source_id": endpoint["source_id"],
        "content_hash": endpoint["content_hash"],
        "disclosable": endpoint["disclosable"],
    }


def bundle_change_evidence(evidence: list[Evidence], report: dict[str, Any], top_k: int) -> list[Evidence]:
    """One package counts as one return item and does not wear a source id."""
    order = report.get("source_order") or []
    if not order:
        return evidence[:top_k]
    by_source: dict[str, Evidence] = {}
    rest: list[Evidence] = []
    for item in evidence:
        if item.source_id in order:
            if item.id == item.source_id and item.source_id not in by_source:
                by_source[item.source_id] = item
            continue
        rest.append(item)
    parts: list = []
    scores = []
    for source_id in order:
        item = by_source.get(source_id)
        if item is None:
            continue
        scores.append(item.score)
        if isinstance(item.content, list):
            parts.extend(item.content)
        elif item.content:
            parts.append({"type": "text", "text": str(item.content)})
    bundle_id = report["bundle_id"]
    if bundle_id in order:
        bundle_id = stable_id("mm-evidence-bundle-v1", bundle_id, "not-a-source")
    bundle = Evidence(bundle_id, parts, None, max(scores) if scores else 0.0, bundle_id)
    return ([bundle] + rest)[: max(top_k, 0)]
