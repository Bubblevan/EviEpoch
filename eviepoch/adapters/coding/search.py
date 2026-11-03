"""Coding Search: obligations, sparse recall, timeline expansion, applicability (TRD §19.5)."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from ...domain import Obligation, QueryPlan, stable_id
from ...retrieval import tokens
from ...domain.requests import SearchRequest
from ...retrieval.evidence import Evidence
from ...retrieval.lexical import terms
from ...storage.view import View
from .lexical import coding_terms
from .models import ApplicabilityAssessment
from .snapshot import ancestry_relation, graph_from_payloads

_BUG = re.compile(r"\b(?:bug|fix|error|fail|crash|regression|null|exception|stack)\b|缺陷|修复|报错|异常", re.I)
_FEATURE = re.compile(r"\b(?:feature|implement|add support|enhancement)\b|功能|实现|新增", re.I)
_ACCEPTANCE = re.compile(r"(?:acceptance|验收)\s*[:：]\s*(.+)", re.I)
_COMMIT = re.compile(r"(?:commit|sha)\s*[:=]\s*([0-9a-f]{7,40})", re.I)
_REPOSITORY = re.compile(r"repository\s*:\s*(\S+)", re.I)
_PATH = re.compile(r"path\s*:\s*(\S+)", re.I)
_SYMBOL = re.compile(r"symbol\s*:\s*(\S+)", re.I)
_ANALOGY = re.compile(r"cross-repo|allow analogy|跨项目", re.I)
_RELATION_SCORE = {
    "same": 1.0,
    "descendant": 0.7,
    "merge": 0.55,
    "ancestor": 0.45,
    "rollback": 0.4,
    "fork": 0.3,
}


def plan_obligations(plan: QueryPlan, query: str) -> QueryPlan:
    intents = list(dict.fromkeys(plan.intents + ["coding"]))
    labels = []
    if _BUG.search(query):
        labels.extend(["reproduction", "root_cause", "implementation_constraints", "failed_attempts", "valid_fix", "regression_tests"])
    if _FEATURE.search(query):
        labels.extend(["requirements", "interface_constraints", "dependencies", "test_adaptation"])
    if not labels:
        labels = ["engineering_context", "patch_evidence", "verification"]
    obligations = list(plan.obligations)
    for index, label in enumerate(labels[:6], start=1):
        obligations.append(Obligation(f"coding-{label}-{index}", f"{label}: {query}"))
    version = query_version(query)
    if _BUG.search(query):
        task_kind = "bugfix"
    elif _FEATURE.search(query):
        task_kind = "feature"
    else:
        task_kind = "unknown"
    return replace(
        plan,
        intents=intents,
        obligations=obligations,
        task_kind=task_kind,
        target_repository=version["repository"],
        target_snapshot=version["commit"],
        affected_symbols=[version["symbol"]] if version["symbol"] else [],
        acceptance=[item.strip() for item in _ACCEPTANCE.findall(query) if item.strip()],
    )


def query_version(query: str) -> dict[str, Any]:
    """Only version conditions written in the query. A path token alone is not one."""
    commit = _COMMIT.search(query)
    repository = _REPOSITORY.search(query)
    path = _PATH.search(query)
    symbol = _SYMBOL.search(query)
    return {
        "commit": commit.group(1).lower() if commit else None,
        "repository": repository.group(1).casefold() if repository else None,
        "path": path.group(1) if path else None,
        "symbol": symbol.group(1) if symbol else None,
        "analogy": bool(_ANALOGY.search(query)),
    }


def version_file_gaps(
    query_files: dict[str, str | None],
    historical_files: dict[str, str | None],
    query_symbols: dict[str, dict],
    historical_symbols: dict[str, dict],
) -> list[str]:
    """Say whether a named file or symbol is still on the query snapshot."""
    gaps: list[str] = []
    for path, historical_hash in historical_files.items():
        if path not in query_files:
            gaps.append(f"file absent on query snapshot: {path}")
        elif query_files[path] and historical_hash and query_files[path] != historical_hash:
            gaps.append(f"file changed: {path}")
    for name, historical in historical_symbols.items():
        current = query_symbols.get(name)
        if current is None:
            gaps.append(f"symbol absent on query snapshot: {name}")
        elif historical.get("rename_status") == "unverified" or current.get("rename_status") == "unverified":
            gaps.append(f"rename unverified: {name}")
    return gaps


def version_channels(view: View, query: str, limit: int) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """Recall snapshots by stored ancestry. No commit in the query means no version hit."""
    wanted = query_version(query)
    if not wanted["commit"]:
        return {}, {}, {}
    payloads, remotes = _stored_snapshots(view)
    by_repo: dict[str, list[dict]] = {}
    for payload in payloads:
        by_repo.setdefault(str(payload.get("repo_id") or ""), []).append(payload)
    graphs = {repo: graph_from_payloads(items) for repo, items in by_repo.items()}
    hits: dict[str, float] = {}
    analogy: dict[str, float] = {}
    relations: dict[str, str] = {}
    for payload in payloads:
        sha = payload.get("commit_sha")
        snapshot_id = payload.get("snapshot_id")
        if not isinstance(sha, str) or not sha or not isinstance(snapshot_id, str):
            continue
        remote = str(remotes.get(payload.get("repo_id")) or "").casefold()
        if wanted["repository"] and remote and remote != wanted["repository"]:
            if wanted["analogy"]:
                analogy[snapshot_id] = 0.2
                relations[snapshot_id] = "other_repository"
            continue
        parents, reverts, trees = graphs.get(str(payload.get("repo_id") or ""), ({}, {}, {}))
        relation = ancestry_relation(wanted["commit"], sha, parents, reverts, trees)
        score = _RELATION_SCORE.get(relation)
        if score is None:
            continue
        hits[snapshot_id] = max(hits.get(snapshot_id, 0.0), score)
        relations[snapshot_id] = relation
    hits = _cap_scores(hits, limit)
    analogy = _cap_scores(analogy, limit)
    return hits, analogy, relations


def _cap_scores(scores: dict[str, float], limit: int) -> dict[str, float]:
    if len(scores) <= limit:
        return scores
    ranked = sorted(scores, key=lambda oid: (-scores[oid], oid))[:limit]
    return {oid: scores[oid] for oid in ranked}


def version_compatible_channel(view: View, query: str, limit: int) -> dict[str, float]:
    hits, _analogy, _relations = version_channels(view, query, limit)
    return hits


def coding_graph_channel(view: View, seed_ids: list[str], limit: int = 24) -> dict[str, float]:
    """Walk stored engineering edges from a hit to its patch, test, dependency, or counterexample."""
    scores: dict[str, float] = {}
    frontier = list(dict.fromkeys(seed_ids))[:12]
    seen = set(frontier)
    for _depth in range(3):
        nxt: list[str] = []
        for oid in frontier:
            rows = _connection(view).execute(
                "SELECT e.target, e.kind FROM ee_coding_edges e "
                "JOIN ee_objects o ON o.ns=e.ns AND o.id=e.target AND o.active>0 "
                "WHERE e.ns=? AND e.origin=? "
                "UNION ALL SELECT e.origin, e.kind FROM ee_coding_edges e "
                "JOIN ee_objects o ON o.ns=e.ns AND o.id=e.origin AND o.active>0 "
                "WHERE e.ns=? AND e.target=?",
                (view.ns, oid, view.ns, oid),
            ).fetchall()
            for other, kind in rows:
                if not isinstance(other, str) or other in seen:
                    continue
                seen.add(other)
                weight = 0.7 if kind in {"VERIFIED_BY", "TESTED_ON", "REVERTS", "EXCEPT_IF"} else 0.45
                scores[other] = max(scores.get(other, 0.0), weight)
                nxt.append(other)
                if len(scores) >= limit:
                    return scores
        frontier = nxt
        if not frontier:
            break
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
            "SELECT e.experience_id FROM ee_experiences e "
            "JOIN ee_objects o ON o.ns=e.ns AND o.id=e.experience_id AND o.active>0 "
            "WHERE e.ns=? AND e.state IN ('contradicted','superseded') AND e.payload LIKE ? LIMIT 8",
            (view.ns, f"%{attempt_id[:12]}%"),
        ).fetchall()
        counter.extend(row[0] for row in rows)
    return list(dict.fromkeys(counter))


def applicability_verdict(
    payload: dict,
    query_sha: str | None,
    *,
    historical_sha: str | None = None,
    parents: dict[str, tuple[str, ...]] | None = None,
    reverts: dict[str, str] | None = None,
    trees: dict[str, str] | None = None,
    query_repo: str | None = None,
    historical_repo: str | None = None,
    file_gaps: list[str] | None = None,
    dirty: bool = False,
) -> tuple[str, list[str], list[str]]:
    """A current patch needs the same repository snapshot and a successful test there.

    Ancestor, fork, rollback, a different repository, or a missing SHA stay out of
    that set. Failure stays visible as something to avoid, not as the fix.
    """
    gaps = list(file_gaps or [])
    if payload.get("applicability") == "incompatible" or payload.get("applied") is False:
        return "incompatible", ["source excludes this attempt from the current solution"], gaps
    if query_repo and historical_repo and query_repo != historical_repo:
        return "incompatible", ["different repository; the same path is not this repo"], gaps
    polarity = payload.get("polarity") or payload.get("outcome")
    failed = polarity in {"failure", "mixed"} or payload.get("exit_code") not in (None, 0)
    sha = historical_sha or payload.get("commit_sha")
    if not query_sha or not isinstance(sha, str) or not sha:
        return "unknown", ["missing version is not a current patch"], gaps + ["query or history has no commit"]
    relation = ancestry_relation(query_sha, sha, parents, reverts, trees)
    reasons = [f"version relation: {relation}"]
    if relation in {"fork", "rollback"}:
        return "incompatible", reasons + ["branch or rollback is not the current patch"], gaps
    if relation != "same":
        gaps.append("historical snapshot is not the query snapshot")
        return "unknown", reasons + ["only the same snapshot can be a current patch"], gaps
    if dirty or payload.get("dirty_overlay_hash"):
        gaps.append("dirty overlay is not the clean query snapshot")
        return "unknown", reasons + ["dirty overlay is not a current patch"], gaps
    if any(token in gap for gap in gaps for token in ("absent", "changed", "unverified")):
        return "unknown", reasons + ["file or symbol no longer matches"], gaps
    if failed:
        return "unknown", reasons + ["failed attempt is evidence to avoid, not a current patch"], gaps
    if not query_repo or not historical_repo:
        gaps.append("repository was not specified")
        return "unknown", reasons + ["repository scope is required"], gaps
    if payload.get("exit_code") == 0 and polarity == "success":
        return "compatible", reasons + ["successful test on the query snapshot"], gaps
    gaps.append("no successful test on the query snapshot")
    return "unknown", reasons, gaps


def compare_applicability(view: View, query: str, candidate_ids: list[str]) -> list[ApplicabilityAssessment]:
    wanted = query_version(query)
    payloads, remotes = _stored_snapshots(view)
    by_id = {item.get("snapshot_id"): item for item in payloads if item.get("snapshot_id")}
    by_repo: dict[str, list[dict]] = {}
    for item in payloads:
        by_repo.setdefault(str(item.get("repo_id") or ""), []).append(item)
    graphs = {repo: graph_from_payloads(items) for repo, items in by_repo.items()}
    assessments: list[ApplicabilityAssessment] = []
    for cid in candidate_ids:
        payload = _load_payload(view, cid)
        if not payload:
            continue
        if payload.get("raw_diff") is None and payload.get("kind") not in (None,) and "commit_sha" not in payload and "exit_code" not in payload and "polarity" not in payload:
            continue
        snapshot = _historical_snapshot(payload, by_id)
        if (
            snapshot is None
            and "exit_code" not in payload
            and "polarity" not in payload
            and "outcome" not in payload
            and not payload.get("raw_diff")
        ):
            continue
        repo_id = str((snapshot or payload).get("repo_id") or "")
        remote = str(remotes.get(repo_id) or "").casefold()
        parents, reverts, trees = graphs.get(repo_id, ({}, {}, {}))
        historical_sha = (snapshot or {}).get("commit_sha") or payload.get("commit_sha")
        gaps = _explicit_file_symbol_gaps(view, wanted, snapshot, payloads, remotes)
        if payload.get("raw_diff") and not payload.get("base_snapshot_id"):
            gaps = [*gaps, "patch has no base snapshot"]
        verdict, reasons, verdict_gaps = applicability_verdict(
            payload,
            wanted["commit"],
            historical_sha=historical_sha if isinstance(historical_sha, str) else None,
            parents=parents,
            reverts=reverts,
            trees=trees,
            query_repo=wanted["repository"],
            historical_repo=remote or None,
            file_gaps=gaps,
            dirty=bool((snapshot or {}).get("dirty_overlay_hash")),
        )
        attempt_id = payload.get("attempt_id") or payload.get("experience_id") or payload.get("test_run_id") or cid
        assessments.append(
            ApplicabilityAssessment(
                f"applic-{str(attempt_id)[:16]}",
                wanted["commit"],
                str(attempt_id),
                verdict,  # type: ignore[arg-type]
                reasons,
                transferable_parts=[payload.get("title", "")] if payload.get("title") else [],
                gaps=verdict_gaps,
            )
        )
    return assessments


def admissibility(assessments: list[ApplicabilityAssessment], query: str) -> dict[str, Any]:
    """Record admissible(H, S). A visible conflict is not treated as collapsed."""
    wanted = query_version(query)
    conflict = any(item.verdict == "compatible" for item in assessments) and any(
        item.verdict == "incompatible" for item in assessments
    )
    scope_ok = bool(wanted["repository"]) or not any(item.verdict == "compatible" for item in assessments)
    matching_test = not any(
        item.verdict == "compatible" and "successful test on the query snapshot" not in item.reasons
        for item in assessments
    )
    patch_base = not any("patch has no base snapshot" in gap for item in assessments for gap in item.gaps)
    collapsed = False
    admissible = scope_ok and matching_test and patch_base and not collapsed
    return {
        "repository_scope_ok": scope_ok,
        "every_verified_claim_has_matching_snapshot_test": matching_test,
        "every_selected_patch_has_base_context": patch_base,
        "no_unresolved_version_conflict_is_silently_collapsed": not collapsed,
        "collapsed_conflict": collapsed,
        "unresolved_conflict": conflict,
        "admissible": admissible,
        "gaps": ["unresolved_version_conflict"] if conflict else [],
    }


def version_hypothesis(view: View, query: str, assessments: list[ApplicabilityAssessment]) -> dict[str, Any]:
    """H is the version assumption. S is the source material that supports or blocks it."""
    wanted = query_version(query)
    applicable = [item.historical_attempt_id for item in assessments if item.verdict == "compatible"]
    conflicts = [item.historical_attempt_id for item in assessments if item.verdict == "incompatible"]
    reproduction: list[str] = []
    causes: list[str] = []
    patches: list[str] = []
    preconditions: list[str] = []
    tests: list[str] = []
    for item in assessments:
        payload = _load_payload(view, item.historical_attempt_id)
        if payload.get("raw_diff"):
            patches.append(item.historical_attempt_id)
        if payload.get("test_run_id") or "exit_code" in payload:
            tests.extend(str(span) for span in (payload.get("output_span_ids") or []) if span)
            if not payload.get("output_span_ids"):
                tests.append(item.historical_attempt_id)
        reproduction.extend(str(span) for span in (payload.get("evidence_span_ids") or []) if span)
        causes.extend(str(span) for span in (payload.get("span_ids") or []) if span)
        preconditions.extend(str(text) for text in (payload.get("premises") or []) if text)
    return {
        "H": {
            "target_snapshot": wanted["commit"],
            "applicable_attempts": list(dict.fromkeys(applicable)),
            "dependency_conditions": {
                key: wanted[key] for key in ("repository", "path", "symbol") if wanted[key]
            },
            "unresolved_conflicts": list(dict.fromkeys(conflicts)) if applicable and conflicts else [],
        },
        "S": {
            "reproduction_spans": list(dict.fromkeys(reproduction)),
            "cause_spans": list(dict.fromkeys(causes)),
            "patch_spans": list(dict.fromkeys(patches)),
            "preconditions": list(dict.fromkeys(preconditions)),
            "test_run_spans": list(dict.fromkeys(tests)),
        },
    }


def applicability_obligations(view: View, assessments: list[ApplicabilityAssessment]) -> list[Obligation]:
    """Failure coverage stays a separate obligation from the current patch."""
    current: list[str] = []
    avoid: list[str] = []
    for item in assessments:
        sources = _source_ids(view, item.historical_attempt_id)
        if item.verdict == "compatible":
            current.extend(sources)
        elif any("failed attempt" in reason for reason in item.reasons):
            avoid.extend(sources)
    obligations: list[Obligation] = []
    if current:
        obligations.append(Obligation(
            "coding-applicability-current",
            "coding applicability current patch",
            alternatives=[[sid] for sid in dict.fromkeys(current)],
        ))
    if avoid:
        obligations.append(Obligation(
            "coding-applicability-avoid",
            "coding applicability avoid repeating",
            alternatives=[[sid] for sid in dict.fromkeys(avoid)],
        ))
    return obligations


def hydrate_exact_evidence(view: View, evidence: list[Evidence]) -> list[Evidence]:
    """Attach the stored diff and test output when the selected excerpt does not already contain them."""
    if getattr(view, "c", None) is None and getattr(view, "repo", None) is not None:
        with view.repo.view(view.ns) as db_view:
            return _hydrate_exact_evidence(db_view, evidence)
    return _hydrate_exact_evidence(view, evidence)


def _hydrate_exact_evidence(view: View, evidence: list[Evidence]) -> list[Evidence]:
    hydrated: list[Evidence] = []
    for item in evidence:
        if not isinstance(item.content, str):
            hydrated.append(item)
            continue
        rows = _connection(view).execute(
            "SELECT o.kind, o.payload FROM ee_dependencies d JOIN ee_objects o ON o.ns=d.ns AND o.id=d.object_id "
            "WHERE d.ns=? AND d.source_id=? AND o.active>0 AND o.kind IN "
            "('coding_patch_attempt','coding_test_run','coding_experience')",
            (view.ns, item.source_id),
        ).fetchall()
        extra: list[str] = []
        for kind, raw in rows:
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            pieces: list[str] = []
            if kind == "coding_patch_attempt" and isinstance(data.get("raw_diff"), str):
                pieces.append(data["raw_diff"])
            if kind == "coding_test_run":
                pieces.extend(_test_run_text(view, data))
            if kind == "coding_experience":
                observed = data.get("observed_result")
                if isinstance(observed, str):
                    pieces.append(observed)
                symptoms = data.get("failure_symptoms")
                if isinstance(symptoms, list):
                    pieces.extend(str(piece) for piece in symptoms if isinstance(piece, str))
            for piece in pieces:
                if piece and piece not in item.content and piece not in extra:
                    extra.append(piece)
        if not extra:
            hydrated.append(item)
            continue
        hydrated.append(replace(item, content=item.content + "\n" + "\n".join(extra)))
    return hydrated


def hydrate_coding_evidence(view: View, evidence: list[Evidence], assessments: list[ApplicabilityAssessment]) -> list[Evidence]:
    """Label linked sources. Repository filtering needs the query, so callers use select_versioned_evidence."""
    return select_versioned_evidence(view, "", evidence, assessments)


def restore_requested_analogy(view: View, query: str, evidence: list[Evidence], analogy_hits: dict[str, float]) -> list[Evidence]:
    """An explicit cross-repo request keeps the analogy source even when the proof selector does not."""
    if not analogy_hits or not query_version(query)["analogy"]:
        return evidence
    present = {item.source_id for item in evidence}
    if getattr(view, "c", None) is None and getattr(view, "repo", None) is not None:
        with view.repo.view(view.ns) as db_view:
            promoted = promote_coding_hits(db_view, analogy_hits)
    else:
        promoted = promote_coding_hits(view, analogy_hits)
    missing = [sid for sid, _score in sorted(promoted.items(), key=lambda item: (-item[1], item[0])) if sid not in present]
    if not missing or not hasattr(view, "snapshot"):
        return evidence
    snap = view.snapshot(set(missing))
    restored = list(evidence)
    for sid in missing:
        source = snap.sources.get(sid)
        if source is None:
            continue
        restored.append(Evidence(sid, source.render(), source.evidence_time, promoted[sid], sid))
    return restored


def select_versioned_evidence(view: View, query: str, evidence: list[Evidence], assessments: list[ApplicabilityAssessment]) -> list[Evidence]:
    """Drop a different repository unless the query asks for an analogy. Keep conflicts."""
    if getattr(view, "c", None) is None and getattr(view, "repo", None) is not None:
        with view.repo.view(view.ns) as db_view:
            return _select_versioned_evidence(db_view, query, evidence, assessments)
    return _select_versioned_evidence(view, query, evidence, assessments)


def _select_versioned_evidence(view: View, query: str, evidence: list[Evidence], assessments: list[ApplicabilityAssessment]) -> list[Evidence]:
    wanted = query_version(query)
    by_source: dict[str, list[ApplicabilityAssessment]] = {}
    for item in assessments:
        for source_id in _source_ids(view, item.historical_attempt_id):
            by_source.setdefault(source_id, []).append(item)
    selected: list[Evidence] = []
    for item in evidence:
        remote = _repository_remote(view, item.source_id)
        if wanted["repository"] and remote and remote != wanted["repository"]:
            if not wanted["analogy"]:
                continue
            selected.append(_annotate(item, ["[analogy]", "[applicability:incompatible] different repository"]))
            continue
        notes = _version_notes(by_source.get(item.source_id, []))
        selected.append(_annotate(item, notes) if notes else item)
    return selected


def _stored_snapshots(view: View) -> tuple[list[dict], dict]:
    rows = _connection(view).execute(
        "SELECT s.payload FROM ee_code_snapshots s "
        "JOIN ee_objects o ON o.ns=s.ns AND o.id=s.snapshot_id AND o.active>0 WHERE s.ns=?",
        (view.ns,),
    ).fetchall()
    remote_rows = _connection(view).execute(
        "SELECT r.repo_id, r.remote FROM ee_repositories r "
        "JOIN ee_objects o ON o.ns=r.ns AND o.id=r.repo_id AND o.active>0 WHERE r.ns=?",
        (view.ns,),
    ).fetchall()
    payloads: list[dict] = []
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            payloads.append(data)
    return payloads, {repo_id: remote for repo_id, remote in remote_rows}


def _historical_snapshot(payload: dict, by_id: dict) -> dict | None:
    for key in ("snapshot_id", "base_snapshot_id"):
        found = by_id.get(payload.get(key))
        if found:
            return found
    if payload.get("commit_sha"):
        return payload
    return None


def _explicit_file_symbol_gaps(view: View, wanted: dict, snapshot: dict | None, payloads: list[dict], remotes: dict) -> list[str]:
    if (not wanted["path"] and not wanted["symbol"]) or not snapshot or not wanted["commit"]:
        return []
    query_snapshot = None
    for item in payloads:
        if item.get("commit_sha") != wanted["commit"] or item.get("dirty_overlay_hash"):
            continue
        remote = str(remotes.get(item.get("repo_id")) or "").casefold()
        if wanted["repository"] and remote and remote != wanted["repository"]:
            continue
        query_snapshot = item
        break
    if query_snapshot is None:
        return []
    query_files: dict[str, str | None] = {}
    historical_files: dict[str, str | None] = {}
    query_symbols: dict[str, dict] = {}
    historical_symbols: dict[str, dict] = {}
    if wanted["path"]:
        rows = _connection(view).execute(
            "SELECT f.snapshot_id, f.content_hash FROM ee_file_revisions f "
            "JOIN ee_objects o ON o.ns=f.ns AND o.id=f.file_id AND o.active>0 "
            "WHERE f.ns=? AND f.path=?",
            (view.ns, wanted["path"]),
        ).fetchall()
        for snapshot_id, digest in rows:
            if snapshot_id == query_snapshot.get("snapshot_id"):
                query_files[wanted["path"]] = digest
            if snapshot_id == snapshot.get("snapshot_id"):
                historical_files[wanted["path"]] = digest
    if wanted["symbol"]:
        rows = _connection(view).execute(
            "SELECT s.snapshot_id, s.payload FROM ee_symbol_revisions s "
            "JOIN ee_objects o ON o.ns=s.ns AND o.id=s.symbol_id AND o.active>0 "
            "WHERE s.ns=? AND s.qualified_name=?",
            (view.ns, wanted["symbol"]),
        ).fetchall()
        for snapshot_id, raw in rows:
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = {}
            record = data if isinstance(data, dict) else {}
            if snapshot_id == query_snapshot.get("snapshot_id"):
                query_symbols[wanted["symbol"]] = record
            if snapshot_id == snapshot.get("snapshot_id"):
                historical_symbols[wanted["symbol"]] = record
    return version_file_gaps(query_files, historical_files, query_symbols, historical_symbols)


def _source_ids(view: View, object_id: str) -> list[str]:
    rows = _connection(view).execute(
        "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id=?",
        (view.ns, object_id),
    ).fetchall()
    return [row[0] for row in rows]


def _repository_remote(view: View, source_id: str) -> str | None:
    row = _connection(view).execute(
        "SELECT o.payload FROM ee_dependencies d JOIN ee_objects o ON o.ns=d.ns AND o.id=d.object_id "
        "WHERE d.ns=? AND d.source_id=? AND o.kind='coding_repository' AND o.active>0 LIMIT 1",
        (view.ns, source_id),
    ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    remote = data.get("remote") if isinstance(data, dict) else None
    return str(remote).casefold() if isinstance(remote, str) and remote else None


def _version_notes(linked: list[ApplicabilityAssessment]) -> list[str]:
    if not linked:
        return []
    verdicts = {item.verdict for item in linked}
    notes: list[str] = []
    if "compatible" in verdicts and "incompatible" in verdicts:
        notes.extend(["[unresolved version conflict]", "[applicability:incompatible]", "[not a current solution]"])
    elif "incompatible" in verdicts:
        notes.extend(["[applicability:incompatible]", "[not a current solution]"])
    elif "compatible" in verdicts:
        return []
    else:
        if any("failed attempt" in " ".join(item.reasons) for item in linked):
            notes.append("[avoid repeating]")
        notes.append("[not a current solution]")
    for item in linked[:4]:
        if item.reasons:
            notes.append(f"[applicability:{item.verdict}] {'; '.join(item.reasons)}")
    return notes


def _annotate(item: Evidence, notes: list[str]) -> Evidence:
    if not notes or not isinstance(item.content, str):
        return item
    return replace(item, content="\n".join(notes) + "\n" + item.content)


_BUGFIX_ROLES = ("reproduction", "cause", "patch", "test")
_FEATURE_ROLES = ("requirements", "constraints", "dependencies", "test")
_ROLE_ORDER = (
    "reproduction", "cause", "precondition", "requirements", "constraints",
    "dependencies", "patch", "test", "failed_attempt", "conflict",
)


def required_evidence_roles(plan: QueryPlan) -> list[str]:
    """Bugfix and feature each have a typed premise set. An unknown task does not invent one."""
    if plan.task_kind == "bugfix":
        return list(_BUGFIX_ROLES)
    if plan.task_kind == "feature":
        return list(_FEATURE_ROLES)
    return []


def evidence_roles(text: str) -> set[str]:
    """Read roles from the source text. A failing run without a diff is the reproduction, not a rejected patch."""
    if not text:
        return set()
    roles: set[str] = set()
    has_diff = "diff --git" in text or "\n+++ " in text
    failed = bool(re.search(r"\bFAILED\b", text) or re.search(r"exit code:\s*[1-9]", text, re.I))
    passed = bool(re.search(r"\bPASSED\b", text) and re.search(r"exit code:\s*0\b", text, re.I))
    if has_diff and not failed:
        roles.add("patch")
    if passed:
        roles.add("test")
    if failed and has_diff:
        roles.add("failed_attempt")
    elif failed:
        roles.add("reproduction")
    if re.search(r"root cause|because\b|premise\s*:|原因", text, re.I):
        roles.add("cause")
    if re.search(r"applicability\s*:|do not reuse|except if|禁止复用|reuse condition", text, re.I):
        roles.add("precondition")
    if "[applicability:incompatible]" in text or re.search(r"version relation: (?:fork|rollback)", text):
        roles.add("conflict")
    if "[avoid repeating]" in text and has_diff:
        roles.add("failed_attempt")
    if re.search(r"requirement\s*:|需求\s*[:：]", text, re.I):
        roles.add("requirements")
    if re.search(r"interface\s*:|接口\s*[:：]", text, re.I):
        roles.add("constraints")
    if re.search(r"depends on|依赖\s*[:：]", text, re.I):
        roles.add("dependencies")
    return roles


def select_typed_evidence(view, evidence: list[Evidence], assessments: list[ApplicabilityAssessment],
                          plan: QueryPlan, config, top_k: int, promoted: dict[str, float], query: str,
                          counterexample_sources: list[str] | None = None):
    """Choose one evidence set three ways from the same candidates, then deliver the typed beam."""
    required = required_evidence_roles(plan)
    pool = _pool_for_roles(view, evidence, assessments, promoted, query, required, counterexample_sources or [])
    role_map = {item.id: evidence_roles(item.content if isinstance(item.content, str) else "") for item in pool}
    counter_ids = set(counterexample_sources or [])
    for item in pool:
        if item.source_id in counter_ids:
            role_map[item.id].add("failed_attempt")
            role_map[item.id].discard("patch")
    candidates = [item.id for item in pool]
    if not required:
        covered = sorted(set().union(*role_map.values()) if role_map else set())
        return list(evidence), {
            "chosen": "identity",
            "candidates": candidates,
            "completeness": {"required": [], "covered": covered, "gaps": []},
        }
    token_budget = tokens.budget(config, query, None)
    topk_ids = _select_topk(pool, top_k, config, token_budget)
    greedy_ids = _select_greedy(pool, role_map, required, top_k, config, token_budget)
    beam_ids, alternatives = _select_typed_beam(pool, role_map, required, top_k, config, token_budget)
    delivered = _deliver(pool, beam_ids, role_map, required, top_k, config, token_budget)
    delivered_ids = _ids_in_delivery(delivered, pool)
    report = {
        "chosen": "typed_beam",
        "candidates": candidates,
        "budget": {"top_k": top_k, "token_budget": token_budget, "char_budget": config.char_budget},
        "topk": {"ids": topk_ids, "covered": _covered(topk_ids, role_map, required)},
        "greedy": {"ids": greedy_ids, "covered": _covered(greedy_ids, role_map, required)},
        "typed_beam": {
            "ids": delivered_ids,
            "covered": _covered(delivered_ids, role_map, required),
            "alternatives": alternatives,
        },
        "completeness": _completeness(required, delivered_ids, role_map, pool),
        "hypotheses": _alternative_hypotheses(view, query, assessments),
        "bundle_id": delivered[0].id if delivered and delivered[0].id not in {item.id for item in pool} else None,
    }
    return delivered, report


def _alternative_hypotheses(view, query: str, assessments: list[ApplicabilityAssessment]) -> list[dict[str, Any]]:
    """Keep the current-patch hypothesis and any avoid/conflict hypothesis beside it."""
    if view is not None and getattr(view, "c", None) is None and getattr(view, "repo", None) is not None:
        with view.repo.view(view.ns) as db_view:
            return _alternative_hypotheses(db_view, query, assessments)
    if view is None:
        base = {
            "target_snapshot": query_version(query)["commit"],
            "applicable_attempts": [],
            "dependency_conditions": {},
            "unresolved_conflicts": [],
        }
    else:
        base = version_hypothesis(view, query, assessments)["H"]
    hypotheses = [{"name": "current_patch", **base}]
    failed = [
        item.historical_attempt_id for item in assessments
        if any("failed attempt" in reason for reason in item.reasons)
    ]
    if failed:
        hypotheses.append({
            "name": "avoid_repeat",
            "target_snapshot": base.get("target_snapshot"),
            "applicable_attempts": [],
            "dependency_conditions": base.get("dependency_conditions", {}),
            "unresolved_conflicts": [],
            "failed_attempts": list(dict.fromkeys(failed)),
        })
    conflicts = list(base.get("unresolved_conflicts") or [])
    if conflicts:
        hypotheses.append({
            "name": "unresolved_conflict",
            "target_snapshot": base.get("target_snapshot"),
            "applicable_attempts": [],
            "dependency_conditions": base.get("dependency_conditions", {}),
            "unresolved_conflicts": conflicts,
        })
    return hypotheses


def _pool_for_roles(view, evidence, assessments, promoted, query, required, counterexample_sources: list[str]) -> list[Evidence]:
    pool = list(evidence)
    if view is None or not required:
        return pool
    present = {item.source_id for item in pool}
    wanted = [sid for sid in list(promoted) + list(counterexample_sources) if sid not in present][:24]
    if not wanted or not hasattr(view, "snapshot"):
        return pool
    snap = view.snapshot(set(wanted))
    loaded = [
        Evidence(sid, source.render(), source.evidence_time, float(promoted.get(sid, 0.0)), sid)
        for sid, source in snap.sources.items()
    ]
    if not loaded:
        return pool
    if getattr(view, "c", None) is not None:
        labeled = _select_versioned_evidence(view, query, loaded, assessments)
    elif getattr(view, "repo", None) is not None:
        with view.repo.view(view.ns) as db_view:
            labeled = _select_versioned_evidence(db_view, query, loaded, assessments)
    else:
        labeled = loaded
    useful = set(required) | {"conflict", "failed_attempt", "precondition"}
    counter = set(counterexample_sources)
    for item in labeled:
        roles = evidence_roles(item.content if isinstance(item.content, str) else "")
        if item.source_id in counter or roles & useful:
            pool.append(item)
    return pool


def _covered(ids: list[str], role_map: dict[str, set[str]], required: list[str]) -> list[str]:
    """A failed diff is evidence to avoid. It does not cover the current patch."""
    found: set[str] = set()
    for eid in ids:
        roles = set(role_map.get(eid, set()))
        if "failed_attempt" in roles:
            roles.discard("patch")
        found |= roles
    return [role for role in required if role in found]


def _test_run_text(view: View, payload: dict) -> list[str]:
    pieces: list[str] = []
    for span_id in payload.get("output_span_ids") or []:
        if not isinstance(span_id, str) or not span_id:
            continue
        row = _connection(view).execute(
            "SELECT payload FROM ee_objects WHERE ns=? AND id=? AND kind='span' AND active>0",
            (view.ns, span_id),
        ).fetchone()
        if not row:
            continue
        try:
            data = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        quote = data.get("quote") if isinstance(data, dict) else None
        if isinstance(quote, str) and quote:
            pieces.append(quote)
    return pieces


def hydrate_candidate_texts(view: View, object_ids: list[str]) -> dict[str, str]:
    """Load exact diff and test-output text for candidates before applicability is judged."""
    texts: dict[str, str] = {}
    for oid in object_ids[:80]:
        payload = _load_payload(view, oid)
        pieces: list[str] = []
        if isinstance(payload.get("raw_diff"), str) and payload["raw_diff"]:
            pieces.append(payload["raw_diff"])
        if payload.get("output_span_ids") or payload.get("test_run_id"):
            pieces.extend(_test_run_text(view, payload))
        if pieces:
            texts[oid] = "\n".join(pieces)
    return texts


def _text(item: Evidence) -> str:
    return item.content if isinstance(item.content, str) else ""


def _fits(items: list[Evidence], top_k: int, config, token_budget: int) -> bool:
    if len(items) > top_k:
        return False
    if sum(len(_text(item)) for item in items) > config.char_budget:
        return False
    return sum(tokens.evidence_cost(config, item) for item in items) <= token_budget


def _pack(items: list[Evidence], role_map: dict[str, set[str]]) -> Evidence:
    ordered = sorted(items, key=lambda item: (_role_rank(role_map.get(item.id, set())), -item.score, item.id))
    body = "\n\n".join(_text(item) for item in ordered if _text(item))
    return Evidence(
        stable_id("coding-bundle-v1", *(item.id for item in ordered)),
        body,
        next((item.created_at for item in ordered if item.created_at), None),
        max(item.score for item in ordered),
        ordered[0].source_id,
    )


def _role_rank(roles: set[str]) -> int:
    for index, role in enumerate(_ROLE_ORDER):
        if role in roles:
            return index
    return len(_ROLE_ORDER)


def _package_members(ids: list[str], role_map: dict[str, set[str]], required: list[str]) -> tuple[list[str], list[str]]:
    current: list[str] = []
    extra: list[str] = []
    package_roles = set(required) | {"precondition"}
    for eid in ids:
        roles = role_map.get(eid, set())
        if "conflict" in roles or ("failed_attempt" in roles and "patch" in roles):
            extra.append(eid)
        elif roles & package_roles:
            current.append(eid)
        else:
            extra.append(eid)
    return current, extra


def _deliver(pool: list[Evidence], ids: list[str], role_map: dict[str, set[str]], required: list[str],
             top_k: int, config, token_budget: int) -> list[Evidence]:
    by_id = {item.id: item for item in pool}
    current, extra = _package_members(ids, role_map, required)
    current_items = [by_id[eid] for eid in current if eid in by_id]
    if len(current_items) >= 2:
        delivered = [_pack(current_items, role_map)]
    else:
        delivered = list(current_items)
    for eid in extra:
        item = by_id.get(eid)
        if item is None:
            continue
        if _fits(delivered + [item], top_k, config, token_budget):
            delivered.append(item)
            continue
        roles = role_map.get(eid, set())
        if delivered and ("conflict" in roles or "failed_attempt" in roles) and _text(item) not in _text(delivered[0]):
            delivered[0] = replace(delivered[0], content=_text(delivered[0]) + "\n\n" + _text(item))
            if not _fits(delivered, top_k, config, token_budget):
                delivered[0] = replace(delivered[0], content=_text(delivered[0])[: -len(_text(item)) - 2])
    return delivered


def _ids_in_delivery(delivered: list[Evidence], pool: list[Evidence]) -> list[str]:
    found: list[str] = []
    for item in pool:
        text = _text(item)
        if text and any(text in _text(delivered_item) for delivered_item in delivered):
            found.append(item.id)
    return found


def _select_topk(pool: list[Evidence], top_k: int, config, token_budget: int) -> list[str]:
    chosen: list[Evidence] = []
    for item in sorted(pool, key=lambda candidate: (-candidate.score, candidate.id)):
        if _fits(chosen + [item], top_k, config, token_budget):
            chosen.append(item)
    return [item.id for item in chosen]


def _select_greedy(pool: list[Evidence], role_map: dict[str, set[str]], required: list[str],
                   top_k: int, config, token_budget: int) -> list[str]:
    chosen: list[str] = []
    remaining = {item.id for item in pool}
    while remaining:
        def gain(eid: str) -> tuple:
            covered = set(_covered(chosen, role_map, required))
            added = len(set(_covered(chosen + [eid], role_map, required)) - covered)
            return (added, role_map.get(eid, set()) != set(), pool_score(eid))

        def pool_score(eid: str) -> float:
            return next(item.score for item in pool if item.id == eid)

        eid = max(remaining, key=lambda candidate: (gain(candidate), candidate))
        if gain(eid)[0] <= 0 and "conflict" not in role_map.get(eid, set()) and "failed_attempt" not in role_map.get(eid, set()):
            break
        delivered = _deliver(pool, chosen + [eid], role_map, required, top_k, config, token_budget)
        if not _fits(delivered, top_k, config, token_budget):
            remaining.remove(eid)
            continue
        chosen.append(eid)
        remaining.remove(eid)
    return _ids_in_delivery(_deliver(pool, chosen, role_map, required, top_k, config, token_budget), pool)


def _select_typed_beam(pool: list[Evidence], role_map: dict[str, set[str]], required: list[str],
                       top_k: int, config, token_budget: int) -> list[str]:
    by_id = [item.id for item in pool]

    def objective(ids: frozenset[str]) -> tuple:
        delivered = _ids_in_delivery(_deliver(pool, list(ids), role_map, required, top_k, config, token_budget), pool)
        roles: set[str] = set()
        for eid in delivered:
            roles |= role_map.get(eid, set())
        noise = sum(1 for eid in delivered if not role_map.get(eid))
        return (
            len([role for role in required if role in roles]),
            1 if "conflict" in roles else 0,
            1 if "failed_attempt" in roles else 0,
            -noise,
            -len(delivered),
        )

    beam = [frozenset()]
    width = max(1, int(getattr(config, "beam_width", 12)))
    for _step in range(max(len(required), 1) + 2):
        options = set(beam)
        for state in beam:
            for eid in by_id:
                if eid in state:
                    continue
                proposed = state | {eid}
                delivered = _deliver(pool, list(proposed), role_map, required, top_k, config, token_budget)
                if _fits(delivered, top_k, config, token_budget):
                    options.add(proposed)
        ranked = sorted(options, key=lambda state: (objective(state), sorted(state)), reverse=True)[:width]
        if ranked == beam:
            break
        beam = ranked
    best = beam[0]
    alternatives = []
    for state in beam:
        delivered_ids = _ids_in_delivery(
            _deliver(pool, list(state), role_map, required, top_k, config, token_budget), pool,
        )
        alternatives.append({"ids": delivered_ids, "covered": _covered(delivered_ids, role_map, required)})
    return _ids_in_delivery(
        _deliver(pool, list(best), role_map, required, top_k, config, token_budget), pool,
    ), alternatives


def _completeness(required, delivered_ids, role_map, pool) -> dict[str, Any]:
    covered = _covered(delivered_ids, role_map, required)
    gaps = []
    for role in required:
        if role in covered:
            continue
        exists = any(role in role_map.get(item.id, set()) for item in pool)
        gaps.append({"role": role, "action": "absent" if not exists else "budget"})
    return {"required": list(required), "covered": covered, "gaps": gaps}
