"""Database DDL and index definitions shared by bootstrap and migration."""
from __future__ import annotations

def schema(pg: bool, dimensions: int) -> str:
    vector_type = f"vector({int(dimensions)})" if pg else "BLOB"
    return f"""
    CREATE TABLE IF NOT EXISTS ee_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ee_namespaces(ns TEXT PRIMARY KEY,generation INTEGER NOT NULL,policy_generation INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ee_requests(ns TEXT NOT NULL,id TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL,
      owner TEXT NOT NULL,lease DOUBLE PRECISION NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_objects(ns TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
      body TEXT NOT NULL,asserted_at TEXT,generation INTEGER NOT NULL,active INTEGER NOT NULL,subject_key TEXT NOT NULL,
      predicate_key TEXT NOT NULL,scope_key TEXT NOT NULL,valid_start TEXT,valid_end TEXT,
      occurrence_start TEXT,occurrence_end TEXT,mention_start TEXT,stored_at TEXT,
      length INTEGER NOT NULL,PRIMARY KEY(ns,id));
    CREATE INDEX IF NOT EXISTS ee_objects_generation ON ee_objects(ns,active,generation);
    CREATE INDEX IF NOT EXISTS ee_objects_kind_generation ON ee_objects(ns,kind,active,generation DESC);
    CREATE INDEX IF NOT EXISTS ee_objects_key ON ee_objects(ns,subject_key,predicate_key,scope_key);
    CREATE INDEX IF NOT EXISTS ee_objects_time ON ee_objects(ns,valid_start);
    CREATE INDEX IF NOT EXISTS ee_objects_active_time ON ee_objects(ns,active,valid_start DESC);
    CREATE TABLE IF NOT EXISTS ee_vectors(ns TEXT NOT NULL,id TEXT NOT NULL,embedding {vector_type} NOT NULL,PRIMARY KEY(ns,id));
    CREATE TABLE IF NOT EXISTS ee_postings(ns TEXT NOT NULL,term TEXT NOT NULL,object_id TEXT NOT NULL,tf INTEGER NOT NULL,
      PRIMARY KEY(ns,term,object_id));
    CREATE TABLE IF NOT EXISTS ee_entity_mentions(ns TEXT NOT NULL,entity_key TEXT NOT NULL,entity_id TEXT,
      object_id TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(ns,entity_key,object_id,role));
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


def temporal_indexes(pg: bool) -> str:
    """Namespace-first temporal indexes; validity intervals remain inclusive ISO dates."""
    return """
    CREATE INDEX IF NOT EXISTS ee_objects_valid_interval ON ee_objects(ns,active,valid_start,valid_end);
    CREATE INDEX IF NOT EXISTS ee_objects_occurrence_interval ON ee_objects(ns,active,occurrence_start,occurrence_end);
    CREATE INDEX IF NOT EXISTS ee_objects_mention_time ON ee_objects(ns,active,mention_start);
    CREATE INDEX IF NOT EXISTS ee_objects_asserted_time ON ee_objects(ns,active,asserted_at);
    CREATE INDEX IF NOT EXISTS ee_objects_stored_time ON ee_objects(ns,active,stored_at);
    """


def graph_indexes() -> str:
    return """
    CREATE INDEX IF NOT EXISTS ee_entity_mentions_key ON ee_entity_mentions(ns,entity_key,object_id);
    CREATE INDEX IF NOT EXISTS ee_entity_mentions_id ON ee_entity_mentions(ns,entity_id,object_id);
    CREATE INDEX IF NOT EXISTS ee_entity_mentions_object ON ee_entity_mentions(ns,object_id);
    """
