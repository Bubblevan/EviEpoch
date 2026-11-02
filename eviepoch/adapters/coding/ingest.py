"""Build coding artifacts, navigation nodes, and evidence spans from sources."""
from __future__ import annotations

from ...domain import Edge, Node, Source, Span, annotate_span, locate, stable_id
from ..base import CodingArtifact, IngestExtension
from .lexical import coding_terms, index_body
from .models import (
    DecisionOutcome,
    EngineeringEvent,
    EngineeringObservation,
    PatchAttempt,
    Repository,
    RepositorySnapshot,
    TestRun,
)
from .claude_observations import artifacts_from_claude_xml
from .native_events import normalize_native_source
from .observation_llm import synthesize_observations
from .parse import parse_source, patch_hash, resolve_repo, resolve_snapshot, span_for_quote
from .reasoning import extract_reasoning_decisions
from .symbols import extract_symbols


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
        if native.tool_output and not parsed.diff_text:
            parsed.test_output = parsed.test_output or native.tool_output
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

        snap_id, provisional, overlay, commit_sha, tree_sha = resolve_snapshot(
            ns, repo_key, parsed, parsed.diff_text
        )
        if snap_id not in snapshots:
            snapshots[snap_id] = RepositorySnapshot(
                snap_id,
                repo_key,
                commit_sha,
                tree_sha,
                parsed.parent_shas,
                dirty_overlay_hash=overlay,
                provisional=provisional,
                source_ids=[source.id],
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

        event_id = stable_id("coding-event-v1", source.id)
        event = EngineeringEvent(
            event_id,
            session_id,
            source.ordinal,
            parsed.tool_name,
            parsed.cwd,
            None,
            snap_id,
            source.asserted_at,
            source_ids=[source.id],
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

        if parsed.diff_text:
            try:
                span = span_for_quote(source, parsed.diff_text[: min(len(parsed.diff_text), 4000)])
            except Exception:
                span = locate(source, source.content[: min(len(source.content), 4000)])
            span = annotate_span(span)
            spans.append(span)
            attempt_id = stable_id("coding-patch-v1", source.id, patch_hash(parsed.diff_text))
            attempt = PatchAttempt(
                attempt_id,
                snap_id,
                parsed.diff_text,
                patch_hash(parsed.diff_text),
                parsed.files_modified or parsed.paths[:16],
                snap_id,
                None,
                "message",
                [source.id],
            )
            artifacts.append(
                _artifact(
                    "patch_attempt",
                    attempt_id,
                    attempt.to_dict(),
                    index_body(*attempt.files, parsed.diff_text[:2000]),
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

        if parsed.test_command or parsed.test_output:
            output_quote = parsed.test_output or source.content
            try:
                test_span = span_for_quote(source, output_quote[: min(len(output_quote), 6000)])
            except Exception:
                test_span = locate(source, source.content[: min(len(source.content), 2000)])
            test_span = annotate_span(test_span)
            spans.append(test_span)
            polarity = "unknown"
            if parsed.exit_code == 0 or (parsed.test_output and "PASSED" in parsed.test_output):
                polarity = "success"
            elif parsed.exit_code not in (None, 0) or (parsed.test_output and "FAILED" in parsed.test_output):
                polarity = "failure"
            test_id = stable_id("coding-test-v1", source.id, parsed.test_command or "test")
            test_run = TestRun(
                test_id,
                parsed.test_command or "test",
                snap_id,
                tree_sha,
                overlay,
                parsed.exit_code,
                [test_span.id],
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
            edges.append(
                Edge(
                    stable_id("coding-edge-v1", test_id, "verified_on", snap_id),
                    test_id,
                    snap_id,
                    "VERIFIED_ON",
                    [source.id],
                )
            )
            state = "tested" if polarity == "success" else "observed"
        else:
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
                state if parsed.test_command or parsed.test_output else "proposed",
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
            causal = "experimentally_supported" if parsed.test_output and parsed.exit_code == 0 else "stated"
            outcome = DecisionOutcome(
                outcome_id,
                parsed.problem or parsed.observation_title or "engineering change",
                parsed.decision or parsed.observation_title or source.content[:400],
                [],
                "success" if parsed.exit_code == 0 else "unknown",
                [],
                [],
                [snap_id] if snap_id else [],
                [],
                causal,  # type: ignore[arg-type]
                [source.id],
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

        for path in set(parsed.files_modified + parsed.files_read):
            snippet = ""
            if path in source.content:
                snippet = path
            file_rev, syms = extract_symbols(path, snippet, snap_id, [source.id])
            artifacts.append(
                _artifact(
                    "file_revision",
                    file_rev.file_id,
                    file_rev.to_dict(),
                    index_body(path, file_rev.language or ""),
                    [source.id],
                    source.asserted_at,
                    path,
                )
            )
            for sym in syms:
                artifacts.append(
                    _artifact(
                        "symbol_revision",
                        sym.symbol_id,
                        sym.to_dict(),
                        index_body(sym.qualified_name, sym.signature, path),
                        [source.id],
                        source.asserted_at,
                        index_body(sym.qualified_name, path),
                    )
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

    return IngestExtension(spans=spans, nodes=nodes, edges=edges, artifacts=artifacts)
