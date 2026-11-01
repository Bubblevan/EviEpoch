"""Explicit PostgreSQL bootstrap. Run with a separate schema-admin URL."""
from __future__ import annotations

import argparse
import json
import os

from ..config import Settings
from ..domain import Source, date_mentions
from ..providers import Providers
from .entities import index_entity_mentions
from .schema import graph_indexes, schema, temporal_indexes

TABLES = ("ee_namespaces", "ee_requests", "ee_objects", "ee_vectors", "ee_postings", "ee_entity_mentions",
          "ee_dependencies", "ee_provenance", "ee_versions", "ee_edges", "ee_tombstones", "ee_operations")


def migrate(admin_url: str, service_role: str, config: Settings):
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(schema(True, config.embed_dimensions))
        _upgrade_temporal_schema(conn)
        identity = json.dumps(Providers(config).embedding_identity(), sort_keys=True)
        row = conn.execute("SELECT value FROM ee_metadata WHERE key='embedding_identity'").fetchone()
        if row and row[0] != identity:
            raise RuntimeError("Embedding identity changed; create and reimport into a new database")
        _partition_vectors(conn, config.embed_dimensions)
        conn.execute("INSERT INTO ee_metadata(key,value) VALUES('embedding_identity',%s) ON CONFLICT DO NOTHING", (identity,))
        conn.execute("INSERT INTO ee_metadata(key,value) VALUES('schema_version','4') "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        role = sql.Identifier(service_role)
        service_password = os.getenv("EPOCH_APP_DB_PASSWORD", "")
        if service_password:
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(role, sql.Literal(service_password)))
        for table in TABLES:
            name = sql.Identifier(table)
            conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(name))
            conn.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(name))
            conn.execute(sql.SQL("DROP POLICY IF EXISTS namespace_isolation ON {}").format(name))
            conn.execute(sql.SQL("CREATE POLICY namespace_isolation ON {} USING "
                                 "(ns=current_setting('eviepoch.namespace',true)) WITH CHECK "
                                 "(ns=current_setting('eviepoch.namespace',true))").format(name))
            conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {} TO {}").format(name, role))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT ON ee_metadata TO {}").format(role))


def _upgrade_temporal_schema(conn):
    """Add temporal and entity indexes, backfilling only from persisted object payloads."""
    for column in ("occurrence_start", "occurrence_end", "mention_start", "stored_at"):
        conn.execute(f"ALTER TABLE ee_objects ADD COLUMN IF NOT EXISTS {column} TEXT")
    conn.execute(temporal_indexes(True))
    conn.execute(graph_indexes())
    row = conn.execute("SELECT value FROM ee_metadata WHERE key='schema_version'").fetchone()
    version = int(row[0]) if row else 0
    if version < 3:
        spans = []
        for ns, oid, kind, raw in conn.execute("SELECT ns,id,kind,payload FROM ee_objects").fetchall():
            data = json.loads(raw)
            if kind == "source":
                source = Source(**{**data, "stored_at": data.get("stored_at", "")})
                mentions = date_mentions(source.content)
                conn.execute("UPDATE ee_objects SET asserted_at=%s,stored_at=%s,mention_start=%s,"
                             "valid_start=NULL,valid_end=NULL WHERE ns=%s AND id=%s",
                             (source.evidence_time, source.stored_at, mentions[0] if mentions else None, ns, oid))
            elif kind == "span":
                spans.append((ns, oid, data))
                mentions = data.get("date_mentions") or date_mentions(data.get("quote", ""))
                conn.execute("UPDATE ee_objects SET mention_start=%s,valid_start=NULL,valid_end=NULL "
                             "WHERE ns=%s AND id=%s",
                             (mentions[0] if mentions else data.get("temporal_start"), ns, oid))
            else:
                occurrence = data.get("occurrence_time") or {}
                conn.execute("UPDATE ee_objects SET occurrence_start=%s,occurrence_end=%s,"
                             "asserted_at=COALESCE(%s,asserted_at) WHERE ns=%s AND id=%s",
                             (occurrence.get("earliest"), occurrence.get("latest"), data.get("asserted_at"), ns, oid))
        for ns, oid, data in spans:
            parent = conn.execute("SELECT asserted_at,stored_at FROM ee_objects WHERE ns=%s AND id=%s",
                                  (ns, data.get("source_id"))).fetchone()
            if parent:
                conn.execute("UPDATE ee_objects SET asserted_at=%s,stored_at=%s WHERE ns=%s AND id=%s",
                             (*parent, ns, oid))
        conn.execute("UPDATE ee_objects o SET stored_at=(SELECT MIN(s.stored_at) FROM ee_dependencies d "
                     "JOIN ee_objects s ON s.ns=d.ns AND s.id=d.source_id AND s.kind='source' "
                     "WHERE d.ns=o.ns AND d.object_id=o.id) WHERE o.stored_at IS NULL")
    if version < 4:
        conn.execute("DELETE FROM ee_entity_mentions")
        for ns, oid, _kind, raw in conn.execute(
                "SELECT ns,id,kind,payload FROM ee_objects WHERE kind NOT IN ('source','span')").fetchall():
            index_entity_mentions(conn, ns, oid, json.loads(raw), pg=True)


def _partition_vectors(conn, dimensions: int, partitions: int = 32):
    """Atomically convert the namespace vector table to hash partitions and build local HNSW indexes."""
    if not 1 <= dimensions <= 2000:
        raise ValueError("pgvector HNSW supports embedding dimensions from 1 to 2000")
    partitioned = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_partitioned_table p "
                               "JOIN pg_class c ON c.oid=p.partrelid WHERE c.relname='ee_vectors')").fetchone()[0]
    if not partitioned:
        conn.execute(f"CREATE TABLE ee_vectors_partitioned(ns TEXT NOT NULL,id TEXT NOT NULL,"
                     f"embedding vector({dimensions}) NOT NULL,PRIMARY KEY(ns,id)) PARTITION BY HASH(ns)")
        for remainder in range(partitions):
            conn.execute(f"CREATE TABLE ee_vectors_p{remainder:02d} PARTITION OF ee_vectors_partitioned "
                         f"FOR VALUES WITH (MODULUS {partitions}, REMAINDER {remainder})")
        old_count = conn.execute("SELECT COUNT(*) FROM ee_vectors").fetchone()[0]
        conn.execute("INSERT INTO ee_vectors_partitioned(ns,id,embedding) SELECT ns,id,embedding FROM ee_vectors")
        new_count = conn.execute("SELECT COUNT(*) FROM ee_vectors_partitioned").fetchone()[0]
        if old_count != new_count:
            raise RuntimeError("vector partition migration did not preserve every embedding")
        conn.execute("DROP TABLE ee_vectors")
        conn.execute("ALTER TABLE ee_vectors_partitioned RENAME TO ee_vectors")
    for remainder in range(partitions):
        table = f"ee_vectors_p{remainder:02d}"
        conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_hnsw ON {table} USING hnsw "
                     "(embedding vector_cosine_ops) WITH (m=16,ef_construction=64)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="eviepoch_app")
    args = parser.parse_args()
    config = Settings.from_env(require_postgres=False)
    admin = os.getenv("EPOCH_ADMIN_DATABASE_URL", "")
    if not admin:
        parser.error("Set EPOCH_ADMIN_DATABASE_URL for migration; never use it as the service URL")
    migrate(admin, args.role, config)
    print("schema v4 ready; vectors are namespace-partitioned with local HNSW indexes and service RLS")


if __name__ == "__main__":
    main()
