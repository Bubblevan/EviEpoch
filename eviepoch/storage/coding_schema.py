"""Schema v5 coding tables (TRD §21.1)."""
from __future__ import annotations


def coding_schema(pg: bool) -> str:
    double = "DOUBLE PRECISION" if pg else "REAL"
    return """
    CREATE TABLE IF NOT EXISTS ee_repositories(
      ns TEXT NOT NULL,
      repo_id TEXT NOT NULL,
      remote TEXT,
      project_label TEXT,
      identity_source TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, repo_id));
    CREATE TABLE IF NOT EXISTS ee_code_snapshots(
      ns TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      repo_id TEXT NOT NULL,
      commit_sha TEXT,
      tree_sha TEXT,
      dirty_overlay_hash TEXT,
      provisional INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, snapshot_id));
    CREATE INDEX IF NOT EXISTS ee_code_snapshots_repo ON ee_code_snapshots(ns,repo_id,generation DESC);
    CREATE TABLE IF NOT EXISTS ee_commit_parents(
      ns TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      parent_sha TEXT NOT NULL,
      generation INTEGER NOT NULL,
      PRIMARY KEY(ns, snapshot_id, parent_sha));
    CREATE TABLE IF NOT EXISTS ee_file_revisions(
      ns TEXT NOT NULL,
      file_id TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      path TEXT NOT NULL,
      language TEXT,
      content_hash TEXT,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, file_id));
    CREATE INDEX IF NOT EXISTS ee_file_revisions_path ON ee_file_revisions(ns,path,snapshot_id);
    CREATE TABLE IF NOT EXISTS ee_symbol_revisions(
      ns TEXT NOT NULL,
      symbol_id TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      file_id TEXT NOT NULL,
      qualified_name TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, symbol_id));
    CREATE INDEX IF NOT EXISTS ee_symbol_revisions_name ON ee_symbol_revisions(ns,qualified_name,snapshot_id);
    CREATE TABLE IF NOT EXISTS ee_engineering_events(
      ns TEXT NOT NULL,
      event_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, event_id));
    CREATE INDEX IF NOT EXISTS ee_engineering_events_session ON ee_engineering_events(ns,session_id,generation);
    CREATE TABLE IF NOT EXISTS ee_patch_attempts(
      ns TEXT NOT NULL,
      attempt_id TEXT NOT NULL,
      base_snapshot_id TEXT,
      patch_hash TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, attempt_id));
    CREATE TABLE IF NOT EXISTS ee_test_runs(
      ns TEXT NOT NULL,
      test_run_id TEXT NOT NULL,
      snapshot_id TEXT,
      exit_code INTEGER,
      outcome TEXT,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, test_run_id));
    CREATE TABLE IF NOT EXISTS ee_experiences(
      ns TEXT NOT NULL,
      experience_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      state TEXT NOT NULL,
      polarity TEXT,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, experience_id));
    CREATE INDEX IF NOT EXISTS ee_experiences_kind ON ee_experiences(ns,kind,state,generation DESC);
    CREATE TABLE IF NOT EXISTS ee_coding_edges(
      ns TEXT NOT NULL,
      id TEXT NOT NULL,
      origin TEXT NOT NULL,
      target TEXT NOT NULL,
      kind TEXT NOT NULL,
      evidence TEXT NOT NULL,
      generation INTEGER NOT NULL,
      PRIMARY KEY(ns, id));
    CREATE INDEX IF NOT EXISTS ee_coding_edges_origin ON ee_coding_edges(ns,origin);
    CREATE INDEX IF NOT EXISTS ee_coding_edges_target ON ee_coding_edges(ns,target);
    CREATE TABLE IF NOT EXISTS ee_coding_blobs(
      ns TEXT NOT NULL,
      blob_id TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      byte_length INTEGER NOT NULL,
      payload TEXT NOT NULL,
      generation INTEGER NOT NULL,
      PRIMARY KEY(ns, blob_id));
    CREATE TABLE IF NOT EXISTS ee_applicability_assessments(
      ns TEXT NOT NULL,
      assessment_id TEXT NOT NULL,
      query_snapshot_id TEXT,
      historical_attempt_id TEXT NOT NULL,
      verdict TEXT NOT NULL,
      generation INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY(ns, assessment_id));
    """
