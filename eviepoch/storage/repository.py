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

from ..domain import (Edge, Governance, IdempotencyConflict, IngestBusy, Node, Snapshot,
                     Source, Span, encode, node_from_dict, stable_id)
from ..retrieval.lexical import frequencies, terms
from ..governance.lifecycle import apply_policy
from ..governance.provenance import attach


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
        row = self._conn.execute("SELECT value FROM ee_metadata WHERE key='embedding_identity'").fetchone()
        if row and row[0] != self.identity:
            self.close()
            raise RuntimeError("Embedding configuration differs; use a new database")
        self._conn.execute("INSERT OR IGNORE INTO ee_metadata(key,value) VALUES('embedding_identity',?)",
                           (self.identity,))

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

    def publish(self, ns: str, request_id: str, owner: str, sources: list[Source], spans: list[Span],
                nodes: list[Node], edges: list[Edge], policies: list[Governance], vectors: dict,
                reconcile) -> int:
        with self.transaction(ns, True) as c:
            suffix = " FOR UPDATE" if self.pg else ""
            generation, policy_generation = c.execute(
                "SELECT generation,policy_generation FROM ee_namespaces WHERE ns=?" + suffix, (ns,)).fetchone()
            ledger = c.execute("SELECT owner,status FROM ee_requests WHERE ns=? AND id=?" + suffix,
                               (ns, request_id)).fetchone()
            if ledger != (owner, "pending"):
                raise IngestBusy("ingestion lease was replaced")
            generation += 1
            view = View(self, c, ns)
            # Coordination runs against the final serialized namespace generation, after model work.
            old = view.related(nodes)
            linked = view.snapshot({oid for edge in edges for oid in (edge.origin, edge.target)})
            old.nodes.update(linked.nodes)
            old.sources.update(linked.sources)
            old.spans.update(linked.spans)
            old.edges = list({e.id: e for e in old.edges + linked.edges}.values())
            nodes, edges = reconcile(nodes, edges, old)
            for source in sources:
                source.generation = generation
                self._put(c, ns, source.id, "source", encode(source), source.content,
                          source.asserted_at, generation, vector=vectors.get(source.id))
            for span in spans:
                self._put(c, ns, span.id, "span", encode(span), "", None, generation,
                          dependencies=[span.source_id])
            for node in nodes:
                node.generation = generation
                self._put(c, ns, node.id, node.kind, encode(node), node.representation(), node.asserted_at,
                          generation, node, node.source_ids, vectors.get(node.id))
            for edge in edges:
                c.execute("INSERT INTO ee_edges(ns,id,origin,target,kind,payload) VALUES(?,?,?,?,?,?) "
                          "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload",
                          (ns, edge.id, edge.origin, edge.target, edge.kind, encode(edge)))
                for sid in edge.source_ids:
                    c.execute("INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?) "
                              "ON CONFLICT DO NOTHING", (ns, edge.id, sid))
                attach(c, ns, edge.id, edge.source_ids, generation)
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
             node=None, dependencies=(), vector=None):
        freq = frequencies(body)
        keys = node.key if node else ("", "", "")
        start = node.valid_time.earliest if node else None
        end = node.valid_time.latest if node else None
        if kind == "summary":
            prior = c.execute("SELECT id FROM ee_objects WHERE ns=? AND kind='summary' AND subject_key=? "
                              "AND predicate_key=? AND scope_key=? AND id<>?", (ns, *keys, oid)).fetchall()
            for previous, in prior:
                for table, column in (("ee_objects", "id"), ("ee_vectors", "id"),
                                      ("ee_postings", "object_id"), ("ee_dependencies", "object_id"),
                                      ("ee_provenance", "id"), ("ee_versions", "id")):
                    c.execute(f"DELETE FROM {table} WHERE ns=? AND {column}=?", (ns, previous))
        c.execute("INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                  "predicate_key,scope_key,valid_start,valid_end,length) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?) "
                  "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload,generation=excluded.generation",
                  (ns, oid, kind, payload, body, asserted_at, generation, *keys, start, end, sum(freq.values())))
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


class Connection:
    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def execute(self, sql, args=()):
        if self.pg:
            return self.raw.execute(sql.replace("?", "%s"), args if args else None)
        return self.raw.execute(sql, args)


class View:
    def __init__(self, repo: Repository, c: Connection, ns: str):
        self.repo, self.c, self.ns = repo, c, ns
        row = c.execute("SELECT generation,policy_generation FROM ee_namespaces WHERE ns=?", (ns,)).fetchone()
        self.generation, self.policy_generation = row or (0, 0)

    def related(self, nodes: list[Node]) -> Snapshot:
        ids = set()
        for key in {n.key for n in nodes}:
            ids.update(r[0] for r in self.c.execute(
                "SELECT id FROM ee_objects WHERE ns=? AND active>0 AND subject_key=? AND predicate_key=? "
                "AND scope_key=? ORDER BY generation DESC LIMIT 256", (self.ns, *key)).fetchall())
        return self.snapshot(ids)

    def context(self, text: str, limit=60) -> Snapshot:
        ranked = self.lexical(text, limit)
        recent = [r[0] for r in self.c.execute(
            "SELECT id FROM ee_objects WHERE ns=? AND active>0 AND kind NOT IN ('source','span','summary') "
            "ORDER BY generation DESC,id LIMIT ?", (self.ns, limit)).fetchall()]
        return self.snapshot(set(ranked) | set(recent))

    def lexical(self, query: str, limit: int) -> dict[str, float]:
        query_terms = list(dict.fromkeys(terms(query)))[:128]
        if not query_terms:
            return {}
        if self.repo.pg:
            # Aggregate postings and IDF in the database; transfer only the bounded candidate pool.
            rows = self.c.execute("""
                WITH stats AS (
                  SELECT COUNT(*)::double precision AS n,AVG(length)::double precision AS avg
                  FROM ee_objects WHERE ns=? AND active>0 AND length>0
                ), df AS (
                  SELECT p.term,COUNT(*)::double precision AS n FROM ee_postings p JOIN ee_objects o
                    ON o.ns=p.ns AND o.id=p.object_id
                  WHERE p.ns=? AND p.term=ANY(?::text[]) AND o.active>0 GROUP BY p.term
                )
                SELECT p.object_id,SUM(LN(1+(s.n-d.n+0.5)/(d.n+0.5))*p.tf*2.2 /
                  (p.tf+1.2*(0.25+0.75*o.length/s.avg))) AS score
                FROM ee_postings p JOIN ee_objects o ON o.ns=p.ns AND o.id=p.object_id
                JOIN df d ON d.term=p.term CROSS JOIN stats s
                WHERE p.ns=? AND p.term=ANY(?::text[]) AND o.active>0
                GROUP BY p.object_id ORDER BY score DESC,p.object_id LIMIT ?
                """, (self.ns, self.ns, query_terms, self.ns, query_terms, limit)).fetchall()
            return {oid: float(score) for oid, score in rows}
        n, avg = self.c.execute("SELECT COUNT(*),AVG(length) FROM ee_objects WHERE ns=? "
                                "AND active>0 AND length>0", (self.ns,)).fetchone()
        if not n:
            return {}
        avg = float(avg)
        scores = {}
        for term in query_terms:
            rows = self.c.execute("SELECT p.object_id,p.tf,o.length FROM ee_postings p JOIN ee_objects o "
                                  "ON o.ns=p.ns AND o.id=p.object_id WHERE p.ns=? AND p.term=? AND o.active>0",
                                  (self.ns, term)).fetchall()
            df = len(rows)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for oid, tf, length in rows:
                scores[oid] = scores.get(oid, 0) + idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
        return dict(sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:limit])

    def dense(self, vector, limit: int) -> dict[str, float]:
        vector = np.asarray(vector, dtype=np.float32)
        if self.repo.pg:
            literal = json.dumps(vector.tolist())
            rows = self.c.execute("SELECT v.id,1-(v.embedding <=> ?::vector) AS score FROM ee_vectors v "
                                  "JOIN ee_objects o ON o.ns=v.ns AND o.id=v.id "
                                  "WHERE v.ns=? AND o.active>0 ORDER BY v.embedding <=> ?::vector,v.id LIMIT ?",
                                  (literal, self.ns, literal, limit)).fetchall()
            return dict(rows)
        rows = self.c.execute("SELECT v.id,v.embedding FROM ee_vectors v JOIN ee_objects o "
                              "ON o.ns=v.ns AND o.id=v.id WHERE v.ns=? AND o.active>0", (self.ns,)).fetchall()
        scores = [(oid, float(np.frombuffer(blob, dtype=np.float32) @ vector)) for oid, blob in rows]
        return dict(sorted(scores, key=lambda x: (-x[1], x[0]))[:limit])

    def temporal(self, date: str | None, limit: int) -> dict[str, float]:
        if not date:
            return {}
        rows = self.c.execute("SELECT id FROM ee_objects WHERE ns=? AND active>0 AND valid_start<=? "
                              "ORDER BY valid_start DESC LIMIT ?", (self.ns, date, limit)).fetchall()
        return {r[0]: 1 / (i + 1) for i, r in enumerate(rows)}

    def snapshot(self, ids: set[str] | None = None, expand: int = 0) -> Snapshot:
        if ids is None:
            rows = self.c.execute("SELECT kind,payload FROM ee_objects WHERE ns=? AND active>0",
                                  (self.ns,)).fetchall()
        else:
            ids = set(ids)
            for _ in range(expand):
                frontier = set()
                for oid in sorted(ids)[:self.repo.config.graph_budget]:
                    for origin, target in self.c.execute(
                        "SELECT origin,target FROM ee_edges WHERE ns=? AND (origin=? OR target=?) LIMIT ?",
                        (self.ns, oid, oid, self.repo.config.graph_budget)).fetchall():
                        frontier.update((origin, target))
                ids.update(sorted(frontier)[:self.repo.config.graph_budget])
            source_ids = set()
            for oid in sorted(ids):
                source_ids.update(r[0] for r in self.c.execute(
                    "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?", (self.ns, oid)).fetchall())
            ids.update(source_ids)
            rows = []
            for oid in sorted(ids):
                row = self.c.execute("SELECT kind,payload FROM ee_objects WHERE ns=? AND id=? AND active>0",
                                     (self.ns, oid)).fetchone()
                if row:
                    rows.append(row)
            # Pull peer assertions anchored in the same sources, and their exact spans.
            for sid in source_ids | {json.loads(p)["id"] for k, p in rows if k == "source"}:
                for row in self.c.execute("SELECT o.kind,o.payload FROM ee_objects o JOIN ee_dependencies d "
                                          "ON d.ns=o.ns AND d.object_id=o.id WHERE d.ns=? AND d.source_id=? "
                                          "AND o.active>0", (self.ns, sid)).fetchall():
                    rows.append(row)
        sources, nodes, spans = {}, {}, {}
        for kind, payload in rows:
            data = json.loads(payload)
            if kind == "source":
                sources[data["id"]] = Source(**data)
            elif kind == "span":
                spans[data["id"]] = Span(**data)
            else:
                nodes[data["id"]] = node_from_dict(data)
        # Fetch dependencies of navigation peers as well. No hidden source can pass assembly.
        for sid in {s for n in nodes.values() for s in n.source_ids} - sources.keys():
            row = self.c.execute("SELECT payload FROM ee_objects WHERE ns=? AND id=? AND kind='source' AND active>0",
                                 (self.ns, sid)).fetchone()
            if row:
                sources[sid] = Source(**json.loads(row[0]))
        nodes = {k: n for k, n in nodes.items() if set(n.source_ids) <= sources.keys()}
        edges = {}
        for oid in nodes:
            for payload, in self.c.execute("SELECT payload FROM ee_edges WHERE ns=? AND (origin=? OR target=?)",
                                           (self.ns, oid, oid)).fetchall():
                edge = Edge(**json.loads(payload))
                if edge.origin in nodes and edge.target in nodes:
                    # The statement establishing a relationship can be a third source, separate
                    # from both endpoint assertions. A valid proof needs that statement too.
                    for sid in set(edge.source_ids) - sources.keys():
                        source_row = self.c.execute("SELECT payload FROM ee_objects WHERE ns=? AND id=? "
                                                    "AND kind='source' AND active>0", (self.ns, sid)).fetchone()
                        if source_row:
                            sources[sid] = Source(**json.loads(source_row[0]))
                if edge.origin in nodes and edge.target in nodes and set(edge.source_ids) <= sources.keys():
                    edges[edge.id] = edge
        return Snapshot(self.generation, self.policy_generation, sources, nodes, list(edges.values()), spans)


def schema(pg: bool, dimensions: int) -> str:
    vector_type = f"vector({int(dimensions)})" if pg else "BLOB"
    return f"""
    CREATE TABLE IF NOT EXISTS ee_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ee_namespaces(ns TEXT PRIMARY KEY,generation INTEGER NOT NULL,policy_generation INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ee_requests(ns TEXT NOT NULL,id TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL,
      owner TEXT NOT NULL,lease DOUBLE PRECISION NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_objects(ns TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
      body TEXT NOT NULL,asserted_at TEXT,generation INTEGER NOT NULL,active INTEGER NOT NULL,subject_key TEXT NOT NULL,
      predicate_key TEXT NOT NULL,scope_key TEXT NOT NULL,valid_start TEXT,valid_end TEXT,length INTEGER NOT NULL,PRIMARY KEY(ns,id));
    CREATE INDEX IF NOT EXISTS ee_objects_generation ON ee_objects(ns,active,generation);
    CREATE INDEX IF NOT EXISTS ee_objects_key ON ee_objects(ns,subject_key,predicate_key,scope_key);
    CREATE INDEX IF NOT EXISTS ee_objects_time ON ee_objects(ns,valid_start);
    CREATE TABLE IF NOT EXISTS ee_vectors(ns TEXT NOT NULL,id TEXT NOT NULL,embedding {vector_type} NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_postings(ns TEXT NOT NULL,term TEXT NOT NULL,object_id TEXT NOT NULL,tf INTEGER NOT NULL,
      PRIMARY KEY(ns,term,object_id));
    CREATE TABLE IF NOT EXISTS ee_dependencies(ns TEXT NOT NULL,object_id TEXT NOT NULL,source_id TEXT NOT NULL,
      PRIMARY KEY(ns,object_id,source_id));
    CREATE INDEX IF NOT EXISTS ee_dependencies_source ON ee_dependencies(ns,source_id);
    CREATE TABLE IF NOT EXISTS ee_provenance(ns TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_versions(ns TEXT NOT NULL,id TEXT NOT NULL,generation INTEGER NOT NULL,payload TEXT NOT NULL,
      PRIMARY KEY(ns,id,generation));
    CREATE TABLE IF NOT EXISTS ee_edges(ns TEXT NOT NULL,id TEXT NOT NULL,origin TEXT NOT NULL,target TEXT NOT NULL,
      kind TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(ns,id));
    CREATE INDEX IF NOT EXISTS ee_edges_origin ON ee_edges(ns,origin);
    CREATE INDEX IF NOT EXISTS ee_edges_target ON ee_edges(ns,target);
    CREATE TABLE IF NOT EXISTS ee_tombstones(ns TEXT NOT NULL,id TEXT NOT NULL,action TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_operations(ns TEXT NOT NULL,id TEXT NOT NULL,generation INTEGER NOT NULL,kind TEXT NOT NULL,
      payload TEXT NOT NULL,PRIMARY KEY(ns,id));
    """
