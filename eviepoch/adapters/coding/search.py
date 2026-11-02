"""Coding Search: obligations, sparse recall, timeline expansion, applicability (TRD §19.5)."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from ...domain import Obligation, QueryPlan
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...retrieval.lexical import terms
from ...storage.view import View
from .lexical import coding_terms
from .models import ApplicabilityAssessment

_BUG = re.compile(r"\b(?:bug|fix|error|fail|crash|regression|null|exception|stack)\b|缺陷|修复|报错|异常", re.I)
_FEATURE = re.compile(r"\b(?:feature|implement|add support|enhancement)\b|功能|实现|新增", re.I)
_COMMIT = re.compile(r"(?:commit|sha)\s*[:=]\s*([0-9a-f]{7,40})", re.I)


def plan_obligations(plan: QueryPlan, query: str) -> QueryPlan:
    intents = list(dict.fromkeys(plan.intents + ["coding"]))
    labels = []
    if _BUG.search(query):
        labels.extend(["reproduction", "root_cause", "failed_attempts", "valid_fix", "regression_tests"])
    if _FEATURE.search(query):
        labels.extend(["requirements", "interface_constraints", "dependencies", "test_adaptation"])
    if not labels:
        labels = ["engineering_context", "patch_evidence", "verification"]
    obligations = list(plan.obligations)
    for index, label in enumerate(labels[:6], start=1):
        obligations.append(Obligation(f"coding-{label}-{index}", f"{label}: {query}"))
    return replace(plan, intents=intents, obligations=obligations)


def version_compatible_channel(view: View, query: str, limit: int) -> dict[str, float]:
    """Version channel: snapshots whose commit/tree match query markers."""
    sha_match = _COMMIT.search(query)
    if not sha_match:
        return {}
    sha = sha_match.group(1).lower()
    scores: dict[str, float] = {}
    rows = _connection(view).execute(
        "SELECT snapshot_id,generation FROM ee_code_snapshots WHERE ns=? AND (commit_sha=? OR tree_sha=?) "
        "ORDER BY generation DESC LIMIT ?",
        (view.ns, sha, sha, limit),
    ).fetchall()
    for rank, (snapshot_id, generation) in enumerate(rows):
        scores[snapshot_id] = 1.0 / (5 + rank) + generation * 1e-6
    child_rows = _connection(view).execute(
        "SELECT snapshot_id,generation FROM ee_commit_parents WHERE ns=? AND parent_sha=? LIMIT ?",
        (view.ns, sha, limit),
    ).fetchall()
    for rank, (snapshot_id, generation) in enumerate(child_rows):
        scores[snapshot_id] = max(scores.get(snapshot_id, 0.0), 0.5 / (5 + rank) + generation * 1e-6)
    return scores


def promote_coding_hits(view: View, scores: dict[str, float]) -> dict[str, float]:
    """Map coding artifact hits onto authoritative source IDs for snapshot hydration."""
    promoted: dict[str, float] = {}
    for oid, score in scores.items():
        row = _connection(view).execute(
            "SELECT kind FROM ee_objects WHERE ns=? AND id=? AND active>0",
            (view.ns, oid),
        ).fetchone()
        if not row:
            continue
        if str(row[0]).startswith("coding_"):
            for (sid,) in _connection(view).execute(
                "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?",
                (view.ns, oid),
            ).fetchall():
                promoted[sid] = max(promoted.get(sid, 0.0), score)
        else:
            promoted[oid] = max(promoted.get(oid, 0.0), score)
    return promoted


def coding_lexical_channel(view: View, query: str, limit: int) -> dict[str, float]:
    wanted = set(terms(query)) | set(coding_terms(query))
    if not wanted:
        return {}
    scores: dict[str, float] = {}
    placeholders = ",".join("?" for _ in wanted)
    rows = _connection(view).execute(
        f"SELECT p.object_id,SUM(p.tf) AS tf,o.generation FROM ee_postings p "
        f"JOIN ee_objects o ON o.ns=p.ns AND o.id=p.object_id "
        f"WHERE p.ns=? AND o.active>0 AND o.kind LIKE 'coding_%' AND p.term IN ({placeholders}) "
        f"GROUP BY p.object_id,o.generation ORDER BY tf DESC,o.generation DESC LIMIT ?",
        (view.ns, *wanted, limit),
    ).fetchall()
    for rank, (oid, tf, generation) in enumerate(rows):
        scores[oid] = max(scores.get(oid, 0.0), float(tf) / (60 + rank) + generation * 1e-6)
    return scores


def _load_payload(view: View, object_id: str) -> dict[str, Any]:
    row = _connection(view).execute(
        "SELECT payload FROM ee_objects WHERE ns=? AND id=? AND active>0",
        (view.ns, object_id),
    ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _connection(view: View):
    inner = getattr(view, "c", None)
    if inner is not None:
        return inner
    raise AttributeError("coding search requires a database view connection")


def expand_attempt_timeline(view: View, seed_ids: list[str], limit: int = 24) -> dict[str, float]:
    """Episode/timeline channel: co-located events and patches for hit observations."""
    scores: dict[str, float] = {}
    for seed in seed_ids[:12]:
        payload = _load_payload(view, seed)
        session_id = payload.get("session_id")
        event_ids = payload.get("related_event_ids") or []
        candidates = [seed, *event_ids]
        if session_id:
            rows = _connection(view).execute(
                "SELECT id,generation FROM ee_objects WHERE ns=? AND active>0 AND kind='coding_engineering_event' "
                "AND payload LIKE ? ORDER BY generation DESC LIMIT ?",
                (view.ns, f'%"session_id": "{session_id}"%', limit),
            ).fetchall()
            candidates.extend(row[0] for row in rows)
        for rank, oid in enumerate(dict.fromkeys(candidates)):
            scores[oid] = max(scores.get(oid, 0.0), 1.0 / (10 + rank))
    return scores


def find_counterexamples(view: View, attempt_ids: list[str]) -> list[str]:
    counter: list[str] = []
    for attempt_id in attempt_ids[:16]:
        rows = _connection(view).execute(
            "SELECT experience_id FROM ee_experiences WHERE ns=? AND state IN ('contradicted','superseded') "
            "AND payload LIKE ? LIMIT 8",
            (view.ns, f"%{attempt_id[:12]}%"),
        ).fetchall()
        counter.extend(row[0] for row in rows)
    return list(dict.fromkeys(counter))


def compare_applicability(view: View, query: str, candidate_ids: list[str]) -> list[ApplicabilityAssessment]:
    query_sha = None
    match = _COMMIT.search(query)
    if match:
        query_sha = match.group(1).lower()
    assessments: list[ApplicabilityAssessment] = []
    for cid in candidate_ids:
        payload = _load_payload(view, cid)
        if payload.get("raw_diff") is None and payload.get("kind") not in (None,):
            continue
        attempt_id = payload.get("attempt_id") or cid
        base = (payload.get("base_snapshot_id") or payload.get("snapshot_id") or "").lower()
        verdict = "unknown"
        reasons: list[str] = []
        gaps: list[str] = []
        if query_sha and base and query_sha not in base and query_sha not in json.dumps(payload).lower():
            verdict = "incompatible"
            reasons.append("query commit/tree not present on historical attempt snapshot")
        elif payload.get("applied") is False:
            verdict = "incompatible"
            reasons.append("patch attempt was not applied")
        elif payload.get("outcome") == "failure" or payload.get("exit_code") not in (None, 0):
            verdict = "compatible"
            reasons.append("failure lesson; verify before reuse")
            gaps.append("needs fresh test run on target snapshot")
        if not reasons:
            reasons.append("insufficient version markers in query and history")
            gaps.append("missing explicit snapshot/test binding")
        assessments.append(
            ApplicabilityAssessment(
                f"applic-{attempt_id[:16]}",
                query_sha,
                attempt_id,
                verdict,  # type: ignore[arg-type]
                reasons,
                transferable_parts=[payload.get("title", "")] if payload.get("title") else [],
                gaps=gaps,
            )
        )
    return assessments


def hydrate_coding_evidence(view: View, evidence: list[Evidence], assessments: list[ApplicabilityAssessment]) -> list[Evidence]:
    """Attach applicability gaps to evidence content without inventing code."""
    if not assessments:
        return evidence
    gap_lines = []
    for item in assessments[:8]:
        if item.verdict == "incompatible":
            gap_lines.append(f"[applicability:incompatible] {item.historical_attempt_id}: {'; '.join(item.reasons)}")
        elif item.gaps:
            gap_lines.append(f"[applicability:{item.verdict}] {item.historical_attempt_id}: {'; '.join(item.gaps)}")
    if not gap_lines:
        return evidence
    note = "\n".join(gap_lines)
    enriched: list[Evidence] = []
    for index, item in enumerate(evidence):
        if index == 0:
            enriched.append(
                Evidence(
                    item.id,
                    item.content + ("\n\n" if item.content else "") + note,
                    item.created_at,
                    item.score,
                    item.source_id,
                    item.source_start,
                    item.source_spans,
                    item.source_ranges,
                    item.source_prefix,
                    item.source_paragraphs,
                )
            )
        else:
            enriched.append(item)
    return enriched
