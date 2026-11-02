"""Persist coding domain rows into schema v5 tables."""
from __future__ import annotations

import json

from ..base import CodingArtifact


def _upsert(connection, sql: str, args: tuple) -> None:
    connection.execute(sql, args)


_ENGINEERING_EDGE_KINDS = {
    "MODIFIES", "CALLS", "DEPENDS_ON", "TESTED_ON", "VERIFIED_BY", "REVERTS", "RESULT_OF", "EXCEPT_IF",
    "REPRODUCED_BY",
}


def persist_coding_edges(connection, ns: str, generation: int, edges) -> None:
    for edge in edges:
        if edge.kind not in _ENGINEERING_EDGE_KINDS:
            continue
        connection.execute(
            "INSERT INTO ee_coding_edges(ns,id,origin,target,kind,evidence,generation) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(ns,id) DO UPDATE SET evidence=excluded.evidence,generation=excluded.generation",
            (ns, edge.id, edge.origin, edge.target, edge.kind, json.dumps(edge.source_ids), generation),
        )


def persist_coding_rows(connection, ns: str, generation: int, artifacts: list[CodingArtifact]) -> None:
    for artifact in artifacts:
        payload = json.dumps(artifact.payload, ensure_ascii=False)
        kind = artifact.kind.removeprefix("coding_")
        if kind == "repository":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_repositories(ns,repo_id,remote,project_label,identity_source,generation,payload) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ns,repo_id) DO UPDATE SET remote=excluded.remote,"
                "project_label=excluded.project_label,generation=excluded.generation,payload=excluded.payload",
                (
                    ns,
                    artifact.id,
                    data.get("remote"),
                    data.get("project_label"),
                    data.get("identity_source", "message"),
                    generation,
                    payload,
                ),
            )
        elif kind == "snapshot":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_code_snapshots(ns,snapshot_id,repo_id,commit_sha,tree_sha,dirty_overlay_hash,"
                "provisional,generation,payload) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ns,snapshot_id) DO UPDATE SET generation=excluded.generation,payload=excluded.payload",
                (
                    ns,
                    artifact.id,
                    data.get("repo_id"),
                    data.get("commit_sha"),
                    data.get("tree_sha"),
                    data.get("dirty_overlay_hash"),
                    1 if data.get("provisional") else 0,
                    generation,
                    payload,
                ),
            )
            for parent in data.get("parent_shas") or []:
                _upsert(
                    connection,
                    "INSERT INTO ee_commit_parents(ns,snapshot_id,parent_sha,generation) VALUES(?,?,?,?) "
                    "ON CONFLICT DO NOTHING",
                    (ns, artifact.id, parent, generation),
                )
        elif kind == "file_revision":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_file_revisions(ns,file_id,snapshot_id,path,language,content_hash,generation,payload) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ns,file_id) DO UPDATE SET generation=excluded.generation,"
                "payload=excluded.payload",
                (
                    ns,
                    artifact.id,
                    data.get("snapshot_id"),
                    data.get("path"),
                    data.get("language"),
                    data.get("content_hash"),
                    generation,
                    payload,
                ),
            )
        elif kind == "symbol_revision":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_symbol_revisions(ns,symbol_id,snapshot_id,file_id,qualified_name,generation,payload) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ns,symbol_id) DO UPDATE SET generation=excluded.generation,"
                "payload=excluded.payload",
                (
                    ns,
                    artifact.id,
                    data.get("snapshot_id"),
                    data.get("file_id"),
                    data.get("qualified_name"),
                    generation,
                    payload,
                ),
            )
        elif kind == "engineering_event":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_engineering_events(ns,event_id,session_id,generation,payload) VALUES(?,?,?,?,?) "
                "ON CONFLICT(ns,event_id) DO UPDATE SET generation=excluded.generation,payload=excluded.payload",
                (ns, artifact.id, data.get("session_id", ""), generation, payload),
            )
        elif kind == "patch_attempt":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_patch_attempts(ns,attempt_id,base_snapshot_id,patch_hash,generation,payload) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(ns,attempt_id) DO UPDATE SET generation=excluded.generation,"
                "payload=excluded.payload",
                (ns, artifact.id, data.get("base_snapshot_id"), data.get("patch_hash", ""), generation, payload),
            )
        elif kind == "test_run":
            data = artifact.payload
            _upsert(
                connection,
                "INSERT INTO ee_test_runs(ns,test_run_id,snapshot_id,exit_code,outcome,generation,payload) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ns,test_run_id) DO UPDATE SET generation=excluded.generation,"
                "payload=excluded.payload",
                (ns, artifact.id, data.get("snapshot_id"), data.get("exit_code"), data.get("outcome"), generation, payload),
            )
        elif kind in {"observation", "decision_outcome"}:
            data = artifact.payload
            experience_kind = "decision_outcome" if kind == "decision_outcome" else data.get("kind", "observation")
            state = data.get("state", "proposed")
            polarity = data.get("polarity")
            _upsert(
                connection,
                "INSERT INTO ee_experiences(ns,experience_id,kind,state,polarity,generation,payload) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ns,experience_id) DO UPDATE SET generation=excluded.generation,"
                "state=excluded.state,payload=excluded.payload",
                (ns, artifact.id, experience_kind, state, polarity, generation, payload),
            )
