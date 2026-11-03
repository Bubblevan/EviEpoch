"""Multimodal Add: media persistence, SGM/OCR indexing (TRD §20.3)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ...config import Settings
from ...domain import Source, Span, annotate_span, locate, stable_id
from ...domain.content import SourceEnvelope, decode_data_uri
from ..base import CodingArtifact, IngestExtension
from .image_identity import read_image_identity
from .media_store import write_asset
from .models import MediaAsset, MediaOccurrence, VisualObservation
from .vision import extract_observation, observation_index_body


def _envelope(source: Source) -> SourceEnvelope:
    locator = source.locator or {}
    data = locator.get("content_envelope")
    if not data:
        return SourceEnvelope([{"type": "text", "text": source.content}])
    parts = []
    for item in data.get("parts", []):
        if item.get("type") == "text":
            parts.append({"type": "text", "text": item.get("text", "")})
        elif item.get("type") == "image_url":
            asset_id = item.get("asset_id")
            if asset_id and item.get("stored"):
                parts.append({"type": "image_url", "asset_id": asset_id})
            else:
                url = item.get("data_uri") or item.get("url")
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
    return SourceEnvelope(parts)


def _adjacent_texts(parts: list, index: int) -> list[str]:
    texts = []
    for offset in (-1, 1):
        pos = index + offset
        if 0 <= pos < len(parts) and parts[pos].get("type") == "text":
            text = parts[pos].get("text", "")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    return texts


def ingest_sources(sources: list[Source], providers=None, *, config: Settings) -> IngestExtension:
    if not sources:
        return IngestExtension()
    ns = sources[0].namespace
    root = Path(config.media_store_path)
    spans: list[Span] = []
    artifacts: list[CodingArtifact] = []

    for source in sources:
        envelope = _envelope(source)
        stored_parts = []
        for index, part in enumerate(envelope.parts):
            if part.get("type") == "text":
                text = part["text"]
                start = source.content.find(text)
                if start < 0:
                    start, end = 0, len(source.content)
                else:
                    end = start + len(text)
                spans.append(annotate_span(locate(source, text, char_start=start, char_end=end)))
                stored_parts.append({"type": "text", "part_ordinal": index, "text": text})
                continue
            if part.get("type") != "image_url":
                continue
            if part.get("asset_id"):
                stored_parts.append(
                    {"type": "image_url", "part_ordinal": index, "asset_id": part["asset_id"], "stored": True}
                )
                continue
            url = part["image_url"]["url"]
            mime, raw = decode_data_uri(url)
            content_hash = hashlib.sha256(raw).hexdigest()
            asset_id = stable_id("media-asset-v1", ns, content_hash)
            path = write_asset(root, ns, content_hash, mime, raw)
            identity = read_image_identity(mime, raw)
            asset = MediaAsset(
                asset_id,
                content_hash,
                mime,
                len(raw),
                str(path),
                source_ids=[source.id],
                width=identity["width"],
                height=identity["height"],
                namespace=ns,
                persistence="staged",
                depicted_date=identity["depicted_date"],
                exif_time=identity["exif_time"],
                captured_at=identity["captured_at"],
            )
            occurrence_id = stable_id("media-occ-v1", source.id, index)
            occurrence = MediaOccurrence(
                occurrence_id,
                asset_id,
                source.id,
                index,
                source.role,
                message_time=source.asserted_at,
                stored_at=source.stored_at,
                depicted_date=identity["depicted_date"],
                exif_time=identity["exif_time"],
                captured_at=identity["captured_at"],
                claimed_event_time=None,
            )
            observed = extract_observation(
                providers,
                url,
                adjacent_texts=_adjacent_texts(envelope.parts, index),
                exif_time=identity["exif_time"],
                width=identity["width"],
                height=identity["height"],
                namespace=ns,
                caption_only=config.multimodal_caption_only,
            )
            fields = observed["fields"]
            observation = VisualObservation(
                stable_id("visual-obs-v1", occurrence_id),
                asset_id,
                occurrence_id,
                fields,
                representation=observed["representation"],
                model=observed["model"],
                prompt_id=observed["prompt_id"],
                confidence=observed["confidence"],
                compared_to_original=observed["compared_to_original"],
                experiment_arm=observed["experiment_arm"],
                ocr_span=observed["ocr_span"],
            )
            body = observation_index_body(fields)
            artifacts.append(
                CodingArtifact(
                    asset_id,
                    "multimodal_asset",
                    asset.to_dict(),
                    f"image {mime} {content_hash[:12]}",
                    [source.id],
                    identity["captured_at"],
                    None,
                )
            )
            artifacts.append(
                CodingArtifact(
                    occurrence_id,
                    "multimodal_occurrence",
                    occurrence.to_dict(),
                    f"occurrence {source.id} part {index}",
                    [source.id],
                    source.asserted_at,
                    None,
                )
            )
            obs_id = observation.observation_id
            artifacts.append(
                CodingArtifact(
                    obs_id,
                    "multimodal_observation",
                    observation.to_dict(),
                    body or f"observation {asset_id}",
                    [source.id],
                    source.asserted_at,
                    body or None,
                )
            )
            stored_parts.append(
                {
                    "type": "image_url",
                    "part_ordinal": index,
                    "asset_id": asset_id,
                    "content_hash": content_hash,
                    "mime": mime,
                    "byte_length": len(raw),
                    "stored": True,
                }
            )
        source.locator["content_envelope"] = {"schema": "source-envelope-v1", "parts": stored_parts}

    return IngestExtension(spans=spans, artifacts=artifacts)
