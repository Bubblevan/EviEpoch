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

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "content_hash": self.content_hash,
            "mime": self.mime,
            "byte_length": self.byte_length,
            "storage_path": self.storage_path,
            "source_ids": list(self.source_ids),
        }


@dataclass
class MediaOccurrence:
    occurrence_id: str
    asset_id: str
    source_id: str
    part_ordinal: int
    role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "asset_id": self.asset_id,
            "source_id": self.source_id,
            "part_ordinal": self.part_ordinal,
            "role": self.role,
        }


@dataclass
class VisualObservation:
    observation_id: str
    asset_id: str
    occurrence_id: str
    fields: dict[str, Any]
    schema_version: str = "sgm-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "asset_id": self.asset_id,
            "occurrence_id": self.occurrence_id,
            "schema_version": self.schema_version,
            "fields": self.fields,
        }
