"""Transactional source/graph store; PostgreSQL is the deployment backend.

SQLite uses the same contracts for reproducible local tests, with an exact dense scan.
The PostgreSQL path performs dense and BM25 candidate discovery inside the database.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..domain import (Edge, Governance, IdempotencyConflict, IngestBusy, Node, PublishConflict, Snapshot,
                     Source, Span, date_mentions, encode, node_from_dict, signal_tags, stable_id)
from ..retrieval.lexical import frequencies, terms
from ..governance.lifecycle import apply_policy
from ..governance.provenance import attach
from .coding_schema import coding_schema
from .multimodal_schema import multimodal_schema
from .schema import graph_indexes, schema, temporal_indexes
from .entities import entity_key as _entity_key, index_entity_mentions
from .view import Connection, ReadSession, View

def _span_index_body(span: Span, sources: dict[str, Source]) -> str:
    source = sources.get(span.source_id)
    metadata = [f"role:{source.role}"] if source else []
    if source and source.evidence_time:
        metadata.append(f"source_time:{source.evidence_time}")
    if span.date_mentions:
        metadata.append("date_mentions:" + ",".join(span.date_mentions))
    metadata.extend(f"signal:{tag}" for tag in span.signal_tags)
    return " ".join([*metadata, span.quote])

def _source_index_body(source: Source) -> str:
    metadata = [f"signal:{tag}" for tag in signal_tags(source.content)]
    dates = date_mentions(source.content)
    if dates:
        metadata.append("date_mentions:" + ",".join(dates))
    return " ".join([source.render(), *metadata])

class Repository:
    def __init__(self, config, identity: dict):
        self.config = config
        self.pg = bool(config.database_url)
        self.identity = json.dumps(identity, sort_keys=True)
        self._lock = threading.RLock()
        self._conn = None

    def open(self):
        if self.pg:
            # Schema changes require a separate migration role, never the service role.
            with self.transaction("") as c:
                role = c.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if role[0] or role[1]:
                    raise RuntimeError("Service database role must not be superuser or BYPASSRLS")
                owned = c.execute("SELECT 1 FROM pg_tables WHERE schemaname=current_schema() "
                                  "AND tablename LIKE 'ee_%' AND tableowner=current_user LIMIT 1").fetchone()
                if owned:
                    raise RuntimeError("Use the non-owner EviEpoch service role")
                row = c.execute("SELECT value FROM ee_metadata WHERE key='embedding_identity'").fetchone()
                if not row or row[0] != self.identity:
                    raise RuntimeError("Run schema migration with matching embedding identity first")
            return
        if self.config.db_path != ":memory:":
            Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.config.db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(schema(False, self.config.embed_dimensions))
        self._ensure_sqlite_search_schema()
        row = self._conn.execute("SELECT value FROM ee_metadata WHERE key='embedding_identity'").fetchone()
        if row and row[0] != self.identity:
            self.close()
            raise RuntimeError("Embedding configuration differs; use a new database")
        self._conn.execute("INSERT OR IGNORE INTO ee_metadata(key,value) VALUES('embedding_identity',?)",
                           (self.identity,))

    def _ensure_sqlite_search_schema(self):
        """Upgrade local temporal and entity indexes without rewriting source evidence."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(ee_objects)")}
        for name in ("occurrence_start", "occurrence_end", "mention_start", "stored_at"):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE ee_objects ADD COLUMN {name} TEXT")
        self._conn.executescript(temporal_indexes(False) + graph_indexes())
        row = self._conn.execute("SELECT value FROM ee_metadata WHERE key='schema_version'").fetchone()
        version = int(row[0]) if row else 0
        if version < 5:
            self._conn.executescript(coding_schema(False))
            self._conn.execute(
                "INSERT INTO ee_metadata(key,value) VALUES('schema_version','5') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            version = 5
        if version < 6:
            self._conn.executescript(multimodal_schema(False))
            self._conn.execute(
                "INSERT INTO ee_metadata(key,value) VALUES('schema_version','6') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            version = 6
        if version < 3:
            spans = []
            for ns, oid, kind, raw in self._conn.execute("SELECT ns,id,kind,payload FROM ee_objects").fetchall():
                data = json.loads(raw)
                if kind == "source":
                    source = Source(**{**data, "stored_at": data.get("stored_at", "")})
                    mentions = date_mentions(source.content)
                    self._conn.execute(
                        "UPDATE ee_objects SET asserted_at=?,stored_at=?,mention_start=?,valid_start=NULL,valid_end=NULL "
                        "WHERE ns=? AND id=?",
                        (source.evidence_time, source.stored_at, mentions[0] if mentions else None, ns, oid))
                elif kind == "span":
                    spans.append((ns, oid, data))
                    mentions = data.get("date_mentions") or date_mentions(data.get("quote", ""))
                    self._conn.execute(
                        "UPDATE ee_objects SET mention_start=?,valid_start=NULL,valid_end=NULL WHERE ns=? AND id=?",
                        (mentions[0] if mentions else data.get("temporal_start"), ns, oid))
                else:
                    occurrence = data.get("occurrence_time") or {}
                    self._conn.execute(
                        "UPDATE ee_objects SET occurrence_start=?,occurrence_end=?,asserted_at=COALESCE(?,asserted_at) "
                        "WHERE ns=? AND id=?",
                        (occurrence.get("earliest"), occurrence.get("latest"), data.get("asserted_at"), ns, oid))
            for ns, oid, data in spans:
                parent = self._conn.execute("SELECT asserted_at,stored_at FROM ee_objects WHERE ns=? AND id=?",
                                            (ns, data.get("source_id"))).fetchone()
                if parent:
                    self._conn.execute("UPDATE ee_objects SET asserted_at=?,stored_at=? WHERE ns=? AND id=?",
                                       (*parent, ns, oid))
            self._conn.execute("UPDATE ee_objects SET stored_at=(SELECT MIN(s.stored_at) FROM ee_dependencies d "
                                "JOIN ee_objects s ON s.ns=d.ns AND s.id=d.source_id AND s.kind='source' "
                                "WHERE d.ns=ee_objects.ns AND d.object_id=ee_objects.id) "
                                "WHERE stored_at IS NULL")
        if version < 4:
            self._conn.execute("DELETE FROM ee_entity_mentions")
            rows = self._conn.execute("SELECT ns,id,kind,payload FROM ee_objects WHERE kind NOT IN "
                                      "('source','span')").fetchall()
            for ns, oid, _kind, raw in rows:
                self._index_entity_mentions(self._conn, ns, oid, json.loads(raw))
        self._conn.execute("INSERT INTO ee_metadata(key,value) VALUES('schema_version','4') "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    @staticmethod
    def _entity_key(value):
        return _entity_key(value)

    @staticmethod
    def _index_entity_mentions(c, ns, oid, data):
        index_entity_mentions(c, ns, oid, data)

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self, ns: str, write: bool = False):
        if self.pg:
            import psycopg
            with psycopg.connect(self.config.database_url) as raw:
                raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" if not write else
                            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                raw.execute("SELECT set_config('eviepoch.namespace',%s,true)", (ns,))
                yield Connection(raw, True)
        else:
            with self._lock:
                if self._conn is None:
                    raise RuntimeError("Repository.open() must be called first")
                self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield Connection(self._conn, False)
                    self._conn.commit()
                except BaseException:
                    self._conn.rollback()
                    raise

    @contextmanager
    def view(self, ns: str):
        with self.transaction(ns) as c:
            yield View(self, c, ns)

    def read_session(self, ns: str):
        """No connection or lock survives a database operation; models run between reads."""
        return ReadSession(self, ns)

    def claim(self, ns: str, request_id: str, payload_hash: str) -> str | None:
        owner = uuid.uuid4().hex
        with self.transaction(ns, True) as c:
            c.execute("INSERT INTO ee_namespaces(ns,generation,policy_generation) VALUES(?,0,0) "
                      "ON CONFLICT(ns) DO NOTHING", (ns,))
            c.execute("INSERT INTO ee_requests(ns,id,payload_hash,status,owner,lease,generation) "
                      "VALUES(?,?,?,'pending',?,?,0) ON CONFLICT(ns,id) DO NOTHING",
                      (ns, request_id, payload_hash, owner, time.time() + 1800))
            suffix = " FOR UPDATE" if self.pg else ""
            row = c.execute("SELECT payload_hash,status,owner,lease FROM ee_requests WHERE ns=? AND id=?" + suffix,
                            (ns, request_id)).fetchone()
            if row[0] != payload_hash:
                raise IdempotencyConflict("request_id already exists with a different payload")
            if row[1] == "committed":
                return None
            if row[2] != owner and row[3] > time.time():
                raise IngestBusy("request is already processing; retry the identical payload")
            c.execute("UPDATE ee_requests SET owner=?,lease=? WHERE ns=? AND id=?",
                      (owner, time.time() + 1800, ns, request_id))
        return owner

    def release(self, ns: str, request_id: str, owner: str):
        with self.transaction(ns, True) as c:
            # Keep the hash: a failed request may only be resumed with the same payload.
            c.execute("UPDATE ee_requests SET lease=0 WHERE ns=? AND id=? AND owner=? AND status='pending'",
                      (ns, request_id, owner))

    def prepare_publish(self, ns: str, nodes: list[Node], edges: list[Edge], reconcile):
        """Reconcile outside the write transaction so every derived object can be indexed first.

        publish() verifies the read generation under lock. A conflict causes the caller to
        rebuild this projection before retrying; committed visibility remains atomic.
        """
        import copy
        prepared_nodes, prepared_edges = copy.deepcopy(nodes), copy.deepcopy(edges)
        with self.view(ns) as view:
            base_generation = view.generation
            old = view.related(prepared_nodes)
            linked = view.snapshot({oid for edge in prepared_edges for oid in (edge.origin, edge.target)})
            old.nodes.update(linked.nodes)
            old.sources.update(linked.sources)
            old.spans.update(linked.spans)
            old.edges = list({e.id: e for e in old.edges + linked.edges}.values())
            prepared_nodes, prepared_edges = reconcile(prepared_nodes, prepared_edges, old)
            present_vectors = {row[0] for row in view._by_ids(
                "SELECT id FROM ee_vectors WHERE ns=? AND id IN ({ids})",
                [node.id for node in prepared_nodes])}
        missing_vectors = {node.id for node in prepared_nodes} - present_vectors
        return base_generation, prepared_nodes, prepared_edges, missing_vectors

    def publish(self, ns: str, request_id: str, owner: str, sources: list[Source], spans: list[Span],
                nodes: list[Node], edges: list[Edge], policies: list[Governance], vectors: dict,
                base_generation: int, coding_artifacts=None) -> int:
        with self.transaction(ns, True) as c:
            suffix = " FOR UPDATE" if self.pg else ""
            generation, policy_generation = c.execute(
                "SELECT generation,policy_generation FROM ee_namespaces WHERE ns=?" + suffix, (ns,)).fetchone()
            ledger = c.execute("SELECT owner,status FROM ee_requests WHERE ns=? AND id=?" + suffix,
                               (ns, request_id)).fetchone()
            if ledger != (owner, "pending"):
                raise IngestBusy("ingestion lease was replaced")
            if generation != base_generation:
                raise PublishConflict("namespace generation changed during derived index preparation")
            generation += 1
            chunked_sources = {source.id for source in sources
                               if len(source.content.encode("utf-8")) > self.config.source_chunk_bytes}
            for source in sources:
                source.generation = generation
                self._put(c, ns, source.id, "source", encode(source), _source_index_body(source),
                          source.evidence_time, generation,
                          vector=None if source.id in chunked_sources else vectors.get(source.id),
                          mention_start=(date_mentions(source.content) or [None])[0], stored_at=source.stored_at)
            source_map = {source.id: source for source in sources}
            for span in spans:
                index_body = _span_index_body(span, source_map)
                source = source_map.get(span.source_id)
                self._put(c, ns, span.id, "span", encode(span), index_body,
                          source.evidence_time if source else None, generation,
                          dependencies=[span.source_id], vector=vectors.get(span.id),
                          mention_start=span.temporal_start, stored_at=source.stored_at if source else None)
            for node in nodes:
                node.generation = generation
                node_sources = [source_map[sid] for sid in node.source_ids if sid in source_map]
                self._put(c, ns, node.id, node.kind, encode(node), node.representation(), node.asserted_at,
                          generation, node, node.source_ids, vectors.get(node.id),
                          stored_at=min((source.stored_at for source in node_sources), default=None))
            for edge in edges:
                c.execute("INSERT INTO ee_edges(ns,id,origin,target,kind,payload) VALUES(?,?,?,?,?,?) "
                          "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload",
                          (ns, edge.id, edge.origin, edge.target, edge.kind, encode(edge)))
                for sid in edge.source_ids:
                    c.execute("INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?) "
                              "ON CONFLICT DO NOTHING", (ns, edge.id, sid))
                attach(c, ns, edge.id, edge.source_ids, generation)
            if coding_artifacts:
                coding_only = [a for a in coding_artifacts if a.kind.startswith("coding_")]
                mm_only = [a for a in coding_artifacts if a.kind.startswith("multimodal_")]
                if coding_only:
                    from ..adapters.coding.storage import put_coding_artifacts
                    put_coding_artifacts(c, ns, generation, coding_only, vectors,
                                         self.config.embed_dimensions, pg=self.pg, edges=edges)
                if mm_only:
                    from ..adapters.multimodal.storage import put_multimodal_artifacts
                    put_multimodal_artifacts(c, ns, generation, mm_only, vectors,
                                             self.config.embed_dimensions, pg=self.pg)
            for policy in policies:
                policy.through_generation = generation
                apply_policy(c, ns, policy, generation)
            if policies:
                policy_generation += 1
            c.execute("UPDATE ee_namespaces SET generation=?,policy_generation=? WHERE ns=?",
                      (generation, policy_generation, ns))
            c.execute("UPDATE ee_requests SET status='committed',generation=?,lease=0 WHERE ns=? AND id=?",
                      (generation, ns, request_id))
            counts = {"sources": len(sources), "nodes": len(nodes), "edges": len(edges),
                      "policies": len(policies), "base_generation": generation - 1}
            c.execute("INSERT INTO ee_operations(ns,id,generation,kind,payload) VALUES(?,?,?,?,?)",
                      (ns, stable_id(ns, request_id, "commit"), generation, "publish", json.dumps(counts)))
        return generation

    def _put(self, c, ns, oid, kind, payload, body, asserted_at, generation,
             node=None, dependencies=(), vector=None, valid_start=None, *, occurrence_start=None,
             occurrence_end=None, mention_start=None, stored_at=None):
        freq = frequencies(body)
        keys = node.key if node else ("", "", "")
        start = node.valid_time.earliest if node else valid_start
        end = node.valid_time.latest if node else None
        occurrence_start = node.occurrence_time.earliest if node else occurrence_start
        occurrence_end = node.occurrence_time.latest if node else occurrence_end
        if kind == "summary":
            prior = c.execute("SELECT id FROM ee_objects WHERE ns=? AND kind='summary' AND subject_key=? "
                              "AND predicate_key=? AND scope_key=? AND id<>?", (ns, *keys, oid)).fetchall()
            for previous, in prior:
                for table, column in (("ee_objects", "id"), ("ee_vectors", "id"),
                                      ("ee_postings", "object_id"), ("ee_dependencies", "object_id"),
                                      ("ee_provenance", "id"), ("ee_versions", "id"),
                                      ("ee_entity_mentions", "object_id")):
                    c.execute(f"DELETE FROM {table} WHERE ns=? AND {column}=?", (ns, previous))
        c.execute("INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                  "predicate_key,scope_key,valid_start,valid_end,occurrence_start,occurrence_end,mention_start,"
                  "stored_at,length) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload,body=excluded.body,"
                  "asserted_at=excluded.asserted_at,generation=excluded.generation,active=1,"
                  "subject_key=excluded.subject_key,predicate_key=excluded.predicate_key,scope_key=excluded.scope_key,"
                  "valid_start=excluded.valid_start,valid_end=excluded.valid_end,"
                  "occurrence_start=excluded.occurrence_start,occurrence_end=excluded.occurrence_end,"
                  "mention_start=excluded.mention_start,stored_at=excluded.stored_at,length=excluded.length",
                  (ns, oid, kind, payload, body, asserted_at, generation, *keys, start, end,
                   occurrence_start, occurrence_end, mention_start, stored_at, sum(freq.values())))
        if node is not None:
            c.execute("DELETE FROM ee_entity_mentions WHERE ns=? AND object_id=?", (ns, oid))
            self._index_entity_mentions(c, ns, oid, asdict(node))
        for sid in dependencies:
            c.execute("INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                      (ns, oid, sid))
        attach(c, ns, oid, dependencies or ([oid] if kind == "source" else []), generation)
        if node is not None:
            c.execute("INSERT INTO ee_versions(ns,id,generation,payload) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                      (ns, oid, generation, payload))
        for term, tf in freq.items():
            c.execute("INSERT INTO ee_postings(ns,term,object_id,tf) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                      (ns, term, oid, tf))
        if vector is not None:
            vec = np.asarray(vector, dtype=np.float32)
            if vec.shape != (self.config.embed_dimensions,) or not np.isfinite(vec).all() or not np.linalg.norm(vec):
                raise ValueError("invalid embedding vector")
            vec = vec / np.linalg.norm(vec)
            value = json.dumps(vec.tolist()) if self.pg else vec.tobytes()
            cast = "?::vector" if self.pg else "?"
            c.execute(f"INSERT INTO ee_vectors(ns,id,embedding) VALUES(?,?,{cast}) ON CONFLICT(ns,id) DO NOTHING",
                      (ns, oid, value))

    def summary_index_metrics(self, ns: str) -> dict:
        """Read live summary index coverage directly from the authoritative database."""
        with self.transaction(ns) as c:
            summary_count = c.execute("SELECT COUNT(*) FROM ee_objects WHERE ns=? AND active>0 AND kind='summary'",
                                      (ns,)).fetchone()[0]
            with_vector = c.execute("SELECT COUNT(*) FROM ee_objects o JOIN ee_vectors v "
                                    "ON v.ns=o.ns AND v.id=o.id WHERE o.ns=? AND o.active>0 AND o.kind='summary'",
                                    (ns,)).fetchone()[0]
            with_lexical = c.execute("SELECT COUNT(DISTINCT o.id) FROM ee_objects o JOIN ee_postings p "
                                     "ON p.ns=o.ns AND p.object_id=o.id WHERE o.ns=? AND o.active>0 "
                                     "AND o.kind='summary'", (ns,)).fetchone()[0]
        return {"summary_count": int(summary_count), "summary_with_vector_count": int(with_vector),
                "summary_lexical_hit_rate": (float(with_lexical) / summary_count if summary_count else None),
                "summary_lexical_metric": "fraction of active summaries with >=1 indexed lexical term"}

    def inspect(self, ns: str) -> dict:
        with self.view(ns) as view:
            snap = view.snapshot()
            operations = [dict(generation=r[0], kind=r[1], details=json.loads(r[2])) for r in view.c.execute(
                "SELECT generation,kind,payload FROM ee_operations WHERE ns=? ORDER BY generation", (ns,)).fetchall()]
            versions = [json.loads(r[0]) for r in view.c.execute(
                "SELECT v.payload FROM ee_versions v JOIN ee_objects o ON o.ns=v.ns AND o.id=v.id "
                "WHERE v.ns=? AND o.active>0 ORDER BY v.generation,v.id", (ns,)).fetchall()]
            return {"generation": snap.generation, "policy_generation": snap.policy_generation,
                    "sources": [asdict(s) for s in snap.sources.values()],
                    "spans": [asdict(s) for s in snap.spans.values()],
                    "nodes": [asdict(n) for n in snap.nodes.values()],
                    "edges": [asdict(e) for e in snap.edges], "operations": operations, "versions": versions}
