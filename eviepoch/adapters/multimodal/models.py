"""Multimodal domain payloads (TRD §20.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MediaAsset:
    asset_id: str
    content_hash: str
    mime: str
    byte_length: int
    storage_path: str
    source_ids: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    namespace: str = ""
    persistence: str = "staged"
    depicted_date: str | None = None
    exif_time: str | None = None
    captured_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "content_hash": self.content_hash,
            "mime": self.mime,
            "real_mime": self.mime,
            "byte_length": self.byte_length,
            "decoded_bytes": self.byte_length,
            "width": self.width,
            "height": self.height,
            "storage_path": self.storage_path,
            "namespace": self.namespace,
            "persistence": self.persistence,
            "source_ids": list(self.source_ids),
            "depicted_date": self.depicted_date,
            "exif_time": self.exif_time,
            "captured_at": self.captured_at,
        }


@dataclass
class MediaOccurrence:
    occurrence_id: str
    asset_id: str
    source_id: str
    part_ordinal: int
    role: str
    message_time: str | None = None
    stored_at: str | None = None
    depicted_date: str | None = None
    exif_time: str | None = None
    captured_at: str | None = None
    claimed_event_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "asset_id": self.asset_id,
            "source_id": self.source_id,
            "part_ordinal": self.part_ordinal,
            "role": self.role,
            "message_time": self.message_time,
            "stored_at": self.stored_at,
            "depicted_date": self.depicted_date,
            "exif_time": self.exif_time,
            "captured_at": self.captured_at,
            "claimed_event_time": self.claimed_event_time,
        }


@dataclass
class VisualObservation:
    observation_id: str
    asset_id: str
    occurrence_id: str
    fields: dict[str, Any]
    schema_version: str = "sgm-v1"
    representation: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    prompt_id: str | None = None
    confidence: float | None = None
    compared_to_original: bool = False
    experiment_arm: str = "multimodal"
    ocr_span: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "asset_id": self.asset_id,
            "occurrence_id": self.occurrence_id,
            "schema_version": self.schema_version,
            "fields": self.fields,
            "representation": self.representation,
            "model": self.model,
            "prompt_id": self.prompt_id,
            "confidence": self.confidence,
            "compared_to_original": self.compared_to_original,
            "experiment_arm": self.experiment_arm,
            "ocr_span": self.ocr_span,
        }
