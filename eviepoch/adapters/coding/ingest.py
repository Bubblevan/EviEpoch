"""Build coding artifacts, navigation nodes, and evidence spans from sources."""
from __future__ import annotations

from ...domain import Edge, Node, Source, Span, annotate_span, stable_id
from ..base import CodingArtifact, IngestExtension
from .lexical import coding_terms, index_body
from .models import (
    DecisionOutcome,
    EngineeringEvent,
    EngineeringObservation,
    FileRevision,
    PatchAttempt,
    Repository,
    RepositorySnapshot,
    TestRun,
)
from .claude_observations import artifacts_from_claude_xml
from .native_events import normalize_native_source
from .observation_llm import synthesize_observations
from .experience import (
    AttemptRecord,
    claims_fix,
    failure_symptoms,
    outcome_polarity,
    short_experiences,
    stated_alternatives,
    stated_premises,
)
from .parse import parse_source, patch_hash, resolve_repo, resolve_snapshot, span_for_quote
from .reasoning import extract_reasoning_decisions
from .snapshot import snapshot_id
from .symbols import diff_hunks, extract_symbols
from .syntax_tree import parse_syntax_tree


def _artifact(kind: str, oid: str, payload: dict, body: str, source_ids: list[str],
              asserted_at: str | None, vector_text: str | None = None) -> CodingArtifact:
    return CodingArtifact(oid, f"coding_{kind}", payload, body, source_ids, asserted_at, vector_text)


def ingest_sources(sources: list[Source], providers=None) -> IngestExtension:
    if not sources:
        return IngestExtension()
    ns = sources[0].namespace
    session_id = sources[0].session_id
    spans: list[Span] = []
    nodes: list[Node] = []
    edges: list[Edge] = []
    artifacts: list[CodingArtifact] = []

    repos: dict[str, Repository] = {}
    snapshots: dict[str, RepositorySnapshot] = {}
    attempts: list[AttemptRecord] = []

    for source in sources:
        native = normalize_native_source(source)
        xml_artifacts, xml_spans = artifacts_from_claude_xml(source)
        artifacts.extend(xml_artifacts)
        spans.extend(xml_spans)
        if providers is not None:
            llm_artifacts, llm_spans = synthesize_observations(source, providers)
            artifacts.extend(llm_artifacts)
            spans.extend(llm_spans)
            artifacts.extend(extract_reasoning_decisions(source, providers))
        parsed = parse_source(source)
        if native.commit_sha:
            parsed.commit_sha = native.commit_sha
        if native.parent_shas:
            parsed.parent_shas = list(dict.fromkeys(parsed.parent_shas + native.parent_shas))
        if native.repository:
            parsed.repo_remote = native.repository
        if native.cwd:
            parsed.cwd = native.cwd
        if native.client and not parsed.client:
            parsed.client = native.client
        if native.tool_call_id and not parsed.tool_call_id:
            parsed.tool_call_id = native.tool_call_id
        if (
            native.tool_output
            and native.tool_output != source.content
            and not parsed.diff_text
            and not parsed.test_output
        ):
            parsed.test_output = native.tool_output
        repo_key, remote, project = resolve_repo(parsed, ns)
        if repo_key not in repos:
            repos[repo_key] = Repository(repo_key, ns, remote, project, source_ids=[source.id])
            artifacts.append(
                _artifact(
                    "repository",
                    repo_key,
                    repos[repo_key].to_dict(),
                    index_body(remote or "", project or "", repo_key),
                    [source.id],
                    source.asserted_at,
                    index_body("repository", remote or project or repo_key),
                )
            )

        snap_id, provisional, overlay, commit_sha, tree_sha, clean_id = resolve_snapshot(
            ns, repo_key, parsed
        )
        parent_shas, reverts = _stored_parents(parsed, commit_sha)
        if snap_id not in snapshots:
            snapshots[snap_id] = RepositorySnapshot(
                snap_id,
                repo_key,
                commit_sha,
                tree_sha,
                parent_shas,
                dirty_overlay_hash=overlay,
                provisional=provisional,
                source_ids=[source.id],
                reverts=reverts,
            )
            artifacts.append(
                _artifact(
                    "snapshot",
                    snap_id,
                    snapshots[snap_id].to_dict(),
                    index_body(commit_sha or "unknown", tree_sha or "unknown", overlay or "", snap_id),
                    [source.id],
                    source.asserted_at,
                    index_body("snapshot", commit_sha or "", tree_sha or "", overlay or ""),
                )
            )
        _store_ancestry(ns, repo_key, parsed, snapshots, artifacts, source)

        input_spans = []
        output_spans = []
        for quote in (parsed.diff_text if parsed.diff_verbatim else None, parsed.uncommitted_diff if parsed.uncommitted_verbatim else None):
            if quote:
                output_spans.append(annotate_span(span_for_quote(source, quote)))
        if parsed.test_output and parsed.test_output in source.content and parsed.test_output != source.content:
            output_spans.append(annotate_span(span_for_quote(source, parsed.test_output)))
        if native.tool_input and native.tool_input in source.content:
            input_spans.append(annotate_span(span_for_quote(source, native.tool_input)))
        spans.extend(input_spans)
        spans.extend(output_spans)

        event_id = stable_id(
            "coding-event-v1",
            ns,
            parsed.client or "",
            parsed.tool_call_id or "",
            source.id,
        )
        event = EngineeringEvent(
            event_id,
            session_id,
            source.ordinal,
            parsed.tool_name,
            parsed.cwd,
            clean_id if overlay else None,
            snap_id,
            source.asserted_at,
            [item.id for item in input_spans],
            [item.id for item in output_spans],
            [source.id],
            parsed.client,
            parsed.tool_call_id,
        )
        artifacts.append(
            _artifact(
                "engineering_event",
                event_id,
                event.to_dict(),
                index_body(parsed.tool_name or "turn", parsed.cwd or "", snap_id),
                [source.id],
                source.asserted_at,
                index_body(parsed.observation_title, *parsed.facts[:8]),
            )
        )

        patch_texts = []
        attempt_ids: list[str] = []
        patch_identity = ""
        if parsed.diff_text:
            patch_texts.append(parsed.diff_text)
        if parsed.uncommitted_diff and parsed.uncommitted_diff not in patch_texts:
            patch_texts.append(parsed.uncommitted_diff)
        for diff_body in patch_texts:
            attempt_id = stable_id("coding-patch-v1", source.id, patch_hash(diff_body))
            attempt_ids.append(attempt_id)
            patch_identity = patch_hash(diff_body)
            attempt = PatchAttempt(
                attempt_id,
                clean_id,
                diff_body,
                patch_hash(diff_body),
                parsed.files_modified or parsed.paths[:16],
                snap_id if overlay and diff_body == parsed.uncommitted_diff else None,
                None,
                "message",
                [source.id],
            )
            artifacts.append(
                _artifact(
                    "patch_attempt",
                    attempt_id,
                    attempt.to_dict(),
                    index_body(*attempt.files, diff_body[:2000]),
                    [source.id],
                    source.asserted_at,
                    index_body("patch", parsed.observation_title, *parsed.files_modified[:8]),
                )
            )
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", attempt_id, "modifies", snap_id),
                    attempt_id,
                    snap_id,
                    "MODIFIES",
                    [source.id],
                )
            )

        span_ids: list[str] = []
        if parsed.test_command or parsed.test_output or parsed.exit_code is not None:
            output_quote = parsed.test_output if parsed.test_output and parsed.test_output in source.content else ""
            quote = output_quote[:6000]
            if quote:
                test_span = annotate_span(span_for_quote(source, quote))
                spans.append(test_span)
                span_ids = [test_span.id]
            polarity = outcome_polarity(parsed.exit_code, parsed.test_output)
            test_id = stable_id("coding-test-v1", source.id, parsed.test_command or "test")
            test_run = TestRun(
                test_id,
                parsed.test_command or "test",
                snap_id,
                tree_sha,
                overlay,
                parsed.exit_code,
                span_ids,
                polarity,
                [source.id],
            )
            artifacts.append(
                _artifact(
                    "test_run",
                    test_id,
                    test_run.to_dict(),
                    index_body(test_run.command, str(parsed.exit_code), output_quote[:4000]),
                    [source.id],
                    source.asserted_at,
                    index_body("test", test_run.command, polarity),
                )
            )
            verified = polarity == "success" and parsed.exit_code == 0
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", test_id, "verified" if verified else "tested", snap_id),
                    test_id,
                    snap_id,
                    "VERIFIED_BY" if verified else "TESTED_ON",
                    [source.id],
                )
            )
            state = "tested" if verified else "observed"
        else:
            test_id = None
            state = "proposed"

        if parsed.observation_kind != "unknown" or parsed.observation_title or parsed.facts:
            obs_id = stable_id("coding-obs-v1", source.id)
            observation = EngineeringObservation(
                obs_id,
                parsed.observation_kind,
                parsed.observation_title or parsed.observation_kind,
                parsed.facts or [source.content[:500]],
                parsed.files_read,
                parsed.files_modified,
                [event_id],
                [],
                state,
                [source.id],
            )
            artifacts.append(
                _artifact(
                    "observation",
                    obs_id,
                    observation.to_dict(),
                    index_body(observation.kind, observation.title, *observation.facts[:12],
                               *observation.files_modified, *observation.files_read),
                    [source.id],
                    source.asserted_at,
                    index_body("observation", observation.kind, observation.title, *observation.facts[:6]),
                )
            )
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", obs_id, "result_of", event_id),
                    obs_id,
                    event_id,
                    "RESULT_OF",
                    [source.id],
                )
            )

        if parsed.decision or parsed.problem:
            outcome_id = stable_id("coding-outcome-v1", source.id)
            decision_polarity = outcome_polarity(parsed.exit_code, parsed.test_output)
            causal = "experimentally_supported" if decision_polarity != "unknown" else "stated"
            outcome = DecisionOutcome(
                outcome_id,
                parsed.problem or parsed.observation_title or "engineering change",
                parsed.decision or parsed.observation_title or source.content[:400],
                stated_alternatives(source.content),
                decision_polarity,  # type: ignore[arg-type]
                failure_symptoms(parsed.test_output),
                list(span_ids),
                [snap_id] if snap_id else [],
                [test_id] if test_id and decision_polarity == "failure" else [],
                causal,  # type: ignore[arg-type]
                [source.id],
                premises=stated_premises(source.content),
                support_ids=[test_id] if test_id and decision_polarity == "success" and parsed.exit_code == 0 else [],
            )
            artifacts.append(
                _artifact(
                    "decision_outcome",
                    outcome_id,
                    outcome.to_dict(),
                    index_body(outcome.problem, outcome.decision, outcome.polarity),
                    [source.id],
                    source.asserted_at,
                    index_body("decision", outcome.problem, outcome.decision),
                )
            )

        claim = claims_fix(source.content, parsed.diff_text, parsed.uncommitted_diff)
        has_test = parsed.test_command or parsed.test_output or parsed.exit_code is not None
        if claim or has_test or patch_identity or parsed.decision or parsed.problem:
            attempts.append(
                AttemptRecord(
                    source.id,
                    source.content,
                    repo_key,
                    snap_id,
                    tool_call_id=parsed.tool_call_id,
                    cwd=parsed.cwd,
                    claim=claim,
                    problem=parsed.problem or parsed.observation_title or "",
                    patch_hash=patch_identity,
                    attempt_ids=attempt_ids,
                    test_run_id=test_id if has_test else None,
                    exit_code=parsed.exit_code,
                    test_output=parsed.test_output,
                    span_ids=[item.id for item in output_spans] + span_ids,
                    event_id=event_id,
                )
            )

        _index_paths(
            source,
            parsed,
            snapshots,
            artifacts,
            edges,
            repo_key,
            snap_id,
            clean_id,
            commit_sha,
            tree_sha,
            parent_shas,
            reverts,
        )

        for term in coding_terms(source.content)[:48]:
            nodes.append(
                Node(
                    stable_id("coding-term-v1", source.id, term),
                    "coding_term",
                    term,
                    "mentions",
                    term,
                    [source.id],
                    [],
                    scope=session_id,
                    tier="scenario",
                    state="navigation",
                    asserted_at=source.asserted_at,
                )
            )

    for experience in short_experiences(attempts):
        artifacts.append(
            _artifact(
                "experience",
                experience.experience_id,
                experience.to_dict(),
                index_body(
                    experience.problem,
                    experience.attempted_change,
                    experience.observed_result[:500],
                    experience.verification,
                    experience.polarity,
                    experience.applicability,
                ),
                list(experience.source_ids),
                None,
                index_body("experience", experience.problem, experience.polarity, experience.state),
            )
        )
        if experience.snapshot_id and experience.verification == "verified":
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", experience.experience_id, "VERIFIED_BY", experience.snapshot_id),
                    experience.experience_id,
                    experience.snapshot_id,
                    "VERIFIED_BY",
                    list(experience.source_ids),
                )
            )
        if experience.snapshot_id and experience.reuse_condition:
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", experience.experience_id, "EXCEPT_IF", experience.snapshot_id),
                    experience.experience_id,
                    experience.snapshot_id,
                    "EXCEPT_IF",
                    list(experience.source_ids),
                )
            )
        if experience.snapshot_id and experience.test_run_ids and experience.verification != "verified":
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", experience.experience_id, "TESTED_ON", experience.snapshot_id),
                    experience.experience_id,
                    experience.snapshot_id,
                    "TESTED_ON",
                    list(experience.source_ids),
                )
            )
    edges.extend(_revert_edges(ns, snapshots))
    return IngestExtension(spans=spans, nodes=nodes, edges=edges, artifacts=artifacts)


def _stored_parents(parsed, commit_sha: str | None) -> tuple[list[str], str | None]:
    parent_shas = list(parsed.parent_shas)
    reverts = parsed.reverts
    recorded = next((item for item in parsed.commit_graph if item.get("sha") == commit_sha), None)
    if recorded:
        if not parent_shas:
            parent_shas = list(recorded.get("parents") or [])
        if not reverts:
            reverts = recorded.get("reverts")
    return parent_shas, reverts


def _store_ancestry(ns, repo_key, parsed, snapshots, artifacts, source) -> None:
    for item in parsed.commit_graph:
        sha = item.get("sha")
        if not isinstance(sha, str) or not sha:
            continue
        tree = item.get("tree")
        sid, provisional = snapshot_id(ns, repo_key, commit_sha=sha, tree_sha=tree, overlay=None)
        if sid in snapshots:
            current = snapshots[sid]
            changed = False
            if item.get("parents") and not current.parent_shas:
                current.parent_shas = list(item["parents"])
                changed = True
            if item.get("reverts") and not current.reverts:
                current.reverts = item["reverts"]
                changed = True
            if changed:
                for artifact in artifacts:
                    if artifact.id == sid:
                        artifact.payload = current.to_dict()
            continue
        snap = RepositorySnapshot(
            sid,
            repo_key,
            sha,
            tree,
            list(item.get("parents") or []),
            dirty_overlay_hash=None,
            provisional=provisional,
            source_ids=[source.id],
            reverts=item.get("reverts"),
        )
        snapshots[sid] = snap
        artifacts.append(
            _artifact(
                "snapshot",
                sid,
                snap.to_dict(),
                index_body(sha, tree or "unknown", sid),
                [source.id],
                source.asserted_at,
                index_body("snapshot", sha, tree or ""),
            )
        )


def _file_bodies(source: Source) -> dict[str, str]:
    locator = source.locator if isinstance(source.locator, dict) else {}
    raw = locator.get("file_bodies")
    if not isinstance(raw, dict):
        return {}
    return {str(path): body for path, body in raw.items() if isinstance(body, str)}


def _ensure_snapshot(snapshots, artifacts, source, sid, repo_key, commit_sha, tree_sha, parent_shas, reverts, provisional) -> None:
    if sid in snapshots:
        return
    snap = RepositorySnapshot(
        sid,
        repo_key,
        commit_sha,
        tree_sha,
        list(parent_shas),
        dirty_overlay_hash=None,
        provisional=provisional,
        source_ids=[source.id],
        reverts=reverts,
    )
    snapshots[sid] = snap
    artifacts.append(
        _artifact(
            "snapshot",
            sid,
            snap.to_dict(),
            index_body(commit_sha or "unknown", tree_sha or "unknown", sid),
            [source.id],
            source.asserted_at,
            index_body("snapshot", commit_sha or "", tree_sha or ""),
        )
    )


def _index_paths(
    source, parsed, snapshots, artifacts, edges, repo_key, snap_id, clean_id,
    commit_sha, tree_sha, parent_shas, reverts,
) -> None:
    bodies = _file_bodies(source)
    hunks = diff_hunks(parsed.diff_text, parsed.uncommitted_diff)
    ordered: list[str] = []
    for path in list(parsed.file_hashes) + list(bodies) + list(parsed.files_modified) + list(parsed.files_read) + list(hunks):
        if path not in ordered:
            ordered.append(path)
    for path in ordered:
        blob = parsed.file_hashes.get(path)
        body = bodies.get(path)
        full = body is not None or (blob and path not in hunks)
        target = clean_id if full else snap_id
        if target != snap_id:
            _ensure_snapshot(
                snapshots,
                artifacts,
                source,
                target,
                repo_key,
                commit_sha,
                tree_sha,
                parent_shas,
                reverts,
                provisional=not commit_sha and not tree_sha,
            )
        syntax = None
        if body is not None:
            file_rev, symbols, calls = extract_symbols(
                path, body, target, [source.id], completeness="full", content_hash=blob, evidence=source.content,
            )
            syntax = parse_syntax_tree(path, body)
        elif blob and path not in hunks:
            file_rev = FileRevision(
                stable_id("coding-file-v1", target, path), target, path, None, blob, [source.id],
            )
            symbols, calls = [], []
        else:
            file_rev, symbols, calls = extract_symbols(
                path,
                hunks.get(path, ""),
                target,
                [source.id],
                completeness="fragment",
                content_hash=None,
                evidence=source.content,
            )
        artifacts.append(
            _artifact(
                "file_revision",
                file_rev.file_id,
                file_rev.to_dict(),
                index_body(path, file_rev.content_hash or file_rev.language or ""),
                [source.id],
                source.asserted_at,
                path,
            )
        )
        for symbol in symbols:
            artifacts.append(
                _artifact(
                    "symbol_revision",
                    symbol.symbol_id,
                    symbol.to_dict(),
                    index_body(symbol.qualified_name, symbol.signature, path),
                    [source.id],
                    source.asserted_at,
                    index_body(symbol.qualified_name, path),
                )
            )
        edges.extend(calls)
        if syntax is not None:
            tree_id = stable_id("coding-syntax-tree-v1", file_rev.file_id)
            artifacts.append(
                _artifact(
                    "syntax_tree",
                    tree_id,
                    {
                        "tree_id": tree_id,
                        "file_id": file_rev.file_id,
                        "snapshot_id": file_rev.snapshot_id,
                        "path": path,
                        "source_ids": [source.id],
                        **syntax,
                    },
                    index_body(path, "syntax", syntax["language"]),
                    [source.id],
                    source.asserted_at,
                    path,
                )
            )


def _revert_edges(ns: str, snapshots: dict) -> list[Edge]:
    by_commit: dict[str, str] = {}
    for snap in snapshots.values():
        if not snap.commit_sha or snap.dirty_overlay_hash:
            continue
        if snap.commit_sha not in by_commit or snap.tree_sha:
            by_commit[snap.commit_sha] = snap.snapshot_id
    edges: list[Edge] = []
    seen: set[str] = set()
    for snap in snapshots.values():
        if not snap.reverts or not snap.commit_sha or snap.dirty_overlay_hash:
            continue
        origin = by_commit.get(snap.commit_sha, snap.snapshot_id)
        target = by_commit.get(snap.reverts)
        if target is None:
            target, _provisional = snapshot_id(ns, snap.repo_id, commit_sha=snap.reverts)
        edge_id = stable_id("coding-edge-v1", origin, "REVERTS", target)
        if edge_id in seen:
            continue
        seen.add(edge_id)
        edges.append(Edge(edge_id, origin, target, "REVERTS", list(snap.source_ids)))
    return edges
