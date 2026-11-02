"""Schema v6 multimodal tables (TRD §21.1)."""
from __future__ import annotations


def multimodal_schema(pg: bool) -> str:
    _ = pg
    return """
    CREATE TABLE IF NOT EXISTS ee_media_assets(
      ns TEXT NOT NULL,
      asset_id TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      mime TEXT NOT NULL,
      byte_length INTEGER NOT NULL,
      width INTEGER,
      height INTEGER,
      storage_path TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, asset_id));
    CREATE INDEX IF NOT EXISTS ee_media_assets_hash ON ee_media_assets(ns,content_hash);
    CREATE TABLE IF NOT EXISTS ee_media_occurrences(
      ns TEXT NOT NULL,
      occurrence_id TEXT NOT NULL,
      asset_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      part_ordinal INTEGER NOT NULL,
      role TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, occurrence_id));
    CREATE INDEX IF NOT EXISTS ee_media_occurrences_source ON ee_media_occurrences(ns,source_id,part_ordinal);
    CREATE INDEX IF NOT EXISTS ee_media_occurrences_asset ON ee_media_occurrences(ns,asset_id);
    """
