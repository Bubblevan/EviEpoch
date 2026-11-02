"""Persist coding artifacts alongside the standard publish transaction."""
from __future__ import annotations

import hashlib
import json

import numpy as np

from ...retrieval.lexical import frequencies
from ..base import CodingArtifact
from .relational import persist_coding_edges, persist_coding_rows

_BLOB_THRESHOLD = 65536


def put_coding_artifacts(connection, ns: str, generation: int, artifacts: list[CodingArtifact],
                         vectors: dict[str, object] | None, dimensions: int, *, pg: bool,
                         edges=None) -> None:
    persist_coding_rows(connection, ns, generation, artifacts)
    if edges:
        persist_coding_edges(connection, ns, generation, edges)
    for artifact in artifacts:
        payload_text = json.dumps(artifact.payload, ensure_ascii=False)
        if len(payload_text.encode("utf-8")) > _BLOB_THRESHOLD:
            digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            blob_id = f"blob-{digest[:32]}"
            connection.execute(
                "INSERT INTO ee_coding_blobs(ns,blob_id,content_hash,byte_length,payload,generation) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(ns,blob_id) DO UPDATE SET generation=excluded.generation",
                (ns, blob_id, digest, len(payload_text.encode("utf-8")), payload_text, generation),
            )
            artifact.payload["blob_id"] = blob_id
            payload_text = json.dumps(artifact.payload, ensure_ascii=False)
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
            raise ValueError("invalid coding embedding vector")
        vec = vec / np.linalg.norm(vec)
        value = json.dumps(vec.tolist()) if pg else vec.tobytes()
        cast = "?::vector" if pg else "?"
        connection.execute(
            f"INSERT INTO ee_vectors(ns,id,embedding) VALUES(?,?,{cast}) ON CONFLICT(ns,id) DO NOTHING",
            (ns, artifact.id, value),
        )
