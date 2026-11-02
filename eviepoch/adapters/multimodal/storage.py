"""Persist multimodal rows and ee_objects navigation artifacts."""
from __future__ import annotations

import json

import numpy as np

from ...retrieval.lexical import frequencies
from ..base import CodingArtifact
def put_multimodal_artifacts(connection, ns: str, generation: int, artifacts: list[CodingArtifact],
                             vectors: dict[str, object] | None, dimensions: int, *, pg: bool) -> None:
    for artifact in artifacts:
        if artifact.kind == "multimodal_asset":
            payload = artifact.payload
            connection.execute(
                "INSERT INTO ee_media_assets(ns,asset_id,content_hash,mime,byte_length,width,height,"
                "storage_path,generation,payload) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ns,asset_id) DO UPDATE SET generation=excluded.generation",
                (
                    ns,
                    payload["asset_id"],
                    payload["content_hash"],
                    payload["mime"],
                    payload["byte_length"],
                    payload.get("width"),
                    payload.get("height"),
                    payload["storage_path"],
                    generation,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        elif artifact.kind == "multimodal_occurrence":
            payload = artifact.payload
            connection.execute(
                "INSERT INTO ee_media_occurrences(ns,occurrence_id,asset_id,source_id,part_ordinal,role,"
                "generation,payload) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ns,occurrence_id) DO UPDATE SET generation=excluded.generation",
                (
                    ns,
                    payload["occurrence_id"],
                    payload["asset_id"],
                    payload["source_id"],
                    payload["part_ordinal"],
                    payload["role"],
                    generation,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        freq = frequencies(artifact.body)
        connection.execute(
            "INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
            "predicate_key,scope_key,valid_start,valid_end,occurrence_start,occurrence_end,mention_start,"
            "stored_at,length) VALUES(?,?,?,?,?,?,?,1,'','','',NULL,NULL,NULL,NULL,NULL,NULL,?) "
            "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload,body=excluded.body,"
            "asserted_at=excluded.asserted_at,generation=excluded.generation,active=1,length=excluded.length",
            (
                ns,
                artifact.id,
                artifact.kind,
                json.dumps(artifact.payload, ensure_ascii=False),
                artifact.body,
                artifact.asserted_at,
                generation,
                sum(freq.values()),
            ),
        )
        for sid in artifact.source_ids:
            connection.execute(
                "INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                (ns, artifact.id, sid),
            )
        for term, tf in freq.items():
            connection.execute(
                "INSERT INTO ee_postings(ns,term,object_id,tf) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                (ns, term, artifact.id, tf),
            )
        vector = (vectors or {}).get(artifact.id)
        if vector is None:
            continue
        vec = np.asarray(vector, dtype=np.float32)
        if vec.shape != (dimensions,) or not np.isfinite(vec).all() or not np.linalg.norm(vec):
            raise ValueError("invalid multimodal embedding vector")
        vec = vec / np.linalg.norm(vec)
        value = json.dumps(vec.tolist()) if pg else vec.tobytes()
        cast = "?::vector" if pg else "?"
        connection.execute(
            f"INSERT INTO ee_vectors(ns,id,embedding) VALUES(?,?,{cast}) ON CONFLICT(ns,id) DO NOTHING",
            (ns, artifact.id, value),
        )
