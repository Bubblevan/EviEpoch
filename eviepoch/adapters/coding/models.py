"""Coding Memory domain objects (TRD §19.2). Serialized in ee_objects.payload."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ObservationKind = Literal[
    "bugfix", "feature", "discovery", "decision", "refactor", "change", "unknown"
]
ExperienceState = Literal["proposed", "observed", "tested", "contradicted", "superseded"]
OutcomePolarity = Literal["success", "failure", "mixed", "unknown"]
Applicability = Literal["compatible", "incompatible", "unknown"]
CausalStrength = Literal["stated", "experimentally_supported", "hypothesized"]


@dataclass
class Repository:
    repo_id: str
    namespace: str
    remote: str | None = None
    project_label: str | None = None
    identity_source: str = "message"
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepositorySnapshot:
    snapshot_id: str
    repo_id: str
    commit_sha: str | None = None
    tree_sha: str | None = None
    parent_shas: list[str] = field(default_factory=list)
    dirty_overlay_hash: str | None = None
    provisional: bool = False
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileRevision:
    file_id: str
    snapshot_id: str
    path: str
    language: str | None = None
    content_hash: str | None = None
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolRevision:
    symbol_id: str
    snapshot_id: str
    file_id: str
    syntax_type: str
    qualified_name: str
    signature: str = ""
    line_start: int | None = None
    line_end: int | None = None
    body_hash: str | None = None
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EngineeringEvent:
    event_id: str
    session_id: str
    turn_ordinal: int
    tool_name: str | None
    cwd: str | None
    before_snapshot_id: str | None
    after_snapshot_id: str | None
    event_time: str | None
    input_span_ids: list[str] = field(default_factory=list)
    output_span_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatchAttempt:
    attempt_id: str
    base_snapshot_id: str | None
    raw_diff: str
    patch_hash: str
    files: list[str] = field(default_factory=list)
    applied_snapshot_id: str | None = None
    applied: bool | None = None
    result_source: str = "unknown"
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestRun:
    test_run_id: str
    command: str
    snapshot_id: str | None
    tree_sha: str | None
    overlay_hash: str | None
    exit_code: int | None
    output_span_ids: list[str] = field(default_factory=list)
    outcome: OutcomePolarity = "unknown"
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EngineeringObservation:
    observation_id: str
    kind: ObservationKind
    title: str
    facts: list[str]
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    related_event_ids: list[str] = field(default_factory=list)
    span_ids: list[str] = field(default_factory=list)
    state: ExperienceState = "proposed"
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionOutcome:
    outcome_id: str
    problem: str
    decision: str
    alternatives: list[str] = field(default_factory=list)
    polarity: OutcomePolarity = "unknown"
    failure_symptoms: list[str] = field(default_factory=list)
    evidence_span_ids: list[str] = field(default_factory=list)
    applicable_snapshot_ids: list[str] = field(default_factory=list)
    counterexample_ids: list[str] = field(default_factory=list)
    causal: CausalStrength = "stated"
    source_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplicabilityAssessment:
    assessment_id: str
    query_snapshot_id: str | None
    historical_attempt_id: str
    verdict: Applicability
    reasons: list[str] = field(default_factory=list)
    transferable_parts: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
