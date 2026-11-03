"""Short experiences and outcome polarity (TRD §19.3).

A stated "fixed" is only a claim. Verification support requires a successful
TestRun on the same snapshot. Separate runs stay separate records.
"""
from __future__ import annotations

import hashlib
import re

from ...domain import stable_id
from .models import ShortExperience

_FIXED = re.compile(r"\bfixed\b|已修复", re.I)
_SYMPTOM = re.compile(r"\b(FAILED|ERROR|AssertionError)\b")
_REASON = re.compile(r"(?m)^reason\s*:\s*(.+)$|\bbecause\b\s+([^.\n]{3,240})", re.I)
_RULE = re.compile(r"(?m)^rule\s*:\s*(.+)$", re.I)
_REUSE = re.compile(r"(?m)^(?:do not reuse(?: when)?|except if|禁止复用)\s*[:：]\s*(.+)$", re.I)
_APPLICABILITY = re.compile(r"(?m)^applicability\s*:\s*(compatible|incompatible|unknown)\s*$", re.I)
_PREMISE = re.compile(r"(?m)^premise\s*:\s*(.+)$", re.I)
_ALTERNATIVE = re.compile(r"(?m)^alternative\s*:\s*(.+)$", re.I)
_SUPERSEDES = re.compile(r"(?m)^supersedes\s*:\s*(\S+)\s*$", re.I)


def claims_fix(content: str, *ignored_regions: str | None) -> bool:
    """A repair claim outside an ignored diff. The word inside a patch is not a claim."""
    visible = content
    for region in ignored_regions:
        if region and region in visible:
            visible = visible.replace(region, " ", 1)
    return bool(_FIXED.search(visible))


def outcome_polarity(exit_code: int | None, test_output: str | None) -> str:
    """Polarity comes from the test result. A missing result stays unknown."""
    output = test_output or ""
    passed = "PASSED" in output
    failed = "FAILED" in output or "ERROR" in output
    if exit_code == 0 and failed:
        return "mixed"
    if exit_code not in (None, 0) and passed and not failed:
        return "mixed"
    if passed and failed:
        return "mixed"
    if exit_code == 0 or passed:
        return "success"
    if exit_code not in (None, 0) or failed:
        return "failure"
    return "unknown"


def prose_outside_diff(content: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in content.splitlines():
        if line.startswith("diff --git "):
            skipping = True
            continue
        if skipping and line.startswith(("diff --git ", "index ", "--- ", "+++ ", "@@ ", "+", "-", " ", "\\")):
            continue
        skipping = False
        kept.append(line)
    return "\n".join(kept)


def stated_reason(content: str) -> str:
    match = _REASON.search(prose_outside_diff(content))
    if not match:
        return ""
    return next(group.strip() for group in match.groups() if group)[:400]


def stated_rule(content: str) -> str:
    match = _RULE.search(prose_outside_diff(content))
    return match.group(1).strip()[:400] if match else ""


def reuse_condition(content: str) -> str:
    match = _REUSE.search(prose_outside_diff(content))
    return match.group(1).strip()[:400] if match else ""


def stated_applicability(content: str) -> str:
    match = _APPLICABILITY.search(prose_outside_diff(content))
    return match.group(1).lower() if match else "unknown"


def stated_premises(content: str) -> list[str]:
    prose = prose_outside_diff(content)
    found = [match.group(1).strip()[:240] for match in _PREMISE.finditer(prose)]
    reason = stated_reason(content)
    if reason and reason not in found:
        found.append(reason)
    return found[:8]


def stated_alternatives(content: str) -> list[str]:
    return [match.group(1).strip()[:240] for match in _ALTERNATIVE.finditer(prose_outside_diff(content))][:8]


def supersedes_token(content: str) -> str:
    match = _SUPERSEDES.search(prose_outside_diff(content))
    return match.group(1).strip() if match else ""


def change_summary(content: str) -> str:
    removed: list[str] = []
    added: list[str] = []
    for line in content.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:].strip())
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:].strip())
    parts: list[str] = []
    if removed:
        parts.append("remove " + "; ".join(removed[:2]))
    if added:
        parts.append("add " + "; ".join(added[:2]))
    return " | ".join(parts)[:400]


def failure_symptoms(test_output: str | None) -> list[str]:
    if not test_output:
        return []
    return [line.strip()[:240] for line in test_output.splitlines() if _SYMPTOM.search(line)][:8]


def verifies_snapshot(exit_code: int | None, test_output: str | None) -> bool:
    """Only exit 0 without a failure line increases verification support."""
    output = test_output or ""
    return exit_code == 0 and "FAILED" not in output and "ERROR" not in output


class AttemptRecord:
    def __init__(
        self,
        source_id: str,
        content: str,
        repo_id: str,
        snapshot_id: str | None,
        *,
        tool_call_id: str | None = None,
        cwd: str | None = None,
        claim: bool = False,
        problem: str = "",
        patch_hash: str = "",
        attempt_ids: list[str] | None = None,
        test_run_id: str | None = None,
        exit_code: int | None = None,
        test_output: str | None = None,
        span_ids: list[str] | None = None,
        event_id: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.content = content
        self.content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.repo_id = repo_id
        self.snapshot_id = snapshot_id
        self.tool_call_id = tool_call_id
        self.cwd = cwd
        self.claim = claim
        self.problem = problem
        self.patch_hash = patch_hash
        self.attempt_ids = list(attempt_ids or [])
        self.test_run_id = test_run_id
        self.exit_code = exit_code
        self.test_output = test_output
        self.span_ids = list(span_ids or [])
        self.event_id = event_id


def _problem(record: AttemptRecord) -> str:
    if record.problem.strip():
        return record.problem.strip()[:800]
    symptoms = failure_symptoms(record.test_output)
    if symptoms:
        return symptoms[0]
    if record.claim:
        return "claimed fix"
    if record.patch_hash:
        return "unspecified change"
    return "unspecified result"


def _base(record: AttemptRecord, occurrence: str) -> ShortExperience:
    polarity = outcome_polarity(record.exit_code, record.test_output) if record.test_run_id else "unknown"
    if record.test_run_id and verifies_snapshot(record.exit_code, record.test_output):
        verification, state, causal = "verified", "tested", "experimentally_supported"
    elif record.test_run_id and polarity != "unknown":
        verification, state, causal = "none", "observed", "experimentally_supported"
    elif record.claim:
        verification, state, causal = "claimed", "proposed", "stated"
    else:
        verification, state, causal = "none", "proposed", "stated"
    observed = (record.test_output or "").strip()[:2000]
    applicability = stated_applicability(record.content)
    return ShortExperience(
        stable_id(
            "coding-experience-v1",
            record.repo_id,
            record.snapshot_id or "",
            record.patch_hash,
            record.cwd or "",
            record.tool_call_id or record.source_id,
        ),
        record.repo_id,
        record.snapshot_id,
        _problem(record),
        change_summary(record.content) or record.patch_hash,
        observed,
        verification,
        polarity,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        causal,  # type: ignore[arg-type]
        applicability,  # type: ignore[arg-type]
        list(record.attempt_ids),
        [record.test_run_id] if record.test_run_id else [],
        [record.event_id] if record.event_id else [],
        list(record.span_ids),
        failure_symptoms(record.test_output),
        [],
        [record.source_id],
        record.cwd,
        occurrence,
        stated_reason(record.content),
        stated_rule(record.content),
        reuse_condition(record.content),
        False,
    )


def short_experiences(records: list[AttemptRecord]) -> list[ShortExperience]:
    """One record per run. Same snapshot success does not absorb a different snapshot or a failure."""
    seen_logs: set[str] = set()
    built: list[tuple[AttemptRecord, ShortExperience]] = []
    for record in records:
        if record.tool_call_id:
            occurrence = "independent"
        elif record.content_hash in seen_logs:
            occurrence = "duplicate_log"
        else:
            occurrence = "independent"
            seen_logs.add(record.content_hash)
        built.append((record, _base(record, occurrence)))

    by_context: dict[tuple[str, str], list[tuple[AttemptRecord, ShortExperience]]] = {}
    for record, experience in built:
        if record.snapshot_id:
            by_context.setdefault((record.snapshot_id, record.cwd or ""), []).append((record, experience))

    for group in by_context.values():
        successes = [
            item for item in group
            if item[0].test_run_id and verifies_snapshot(item[0].exit_code, item[0].test_output)
        ]
        failures = [
            item for item in group
            if item[0].test_run_id and outcome_polarity(item[0].exit_code, item[0].test_output) == "failure"
        ]
        for record, experience in group:
            if record.test_run_id or not record.claim:
                continue
            if successes and not failures:
                experience.verification = "verified"
                experience.state = "tested"
                experience.polarity = "success"
                experience.causal = "experimentally_supported"
                for passed_record, _passed in successes:
                    if passed_record.test_run_id and passed_record.test_run_id not in experience.test_run_ids:
                        experience.test_run_ids.append(passed_record.test_run_id)
                    if passed_record.source_id not in experience.source_ids:
                        experience.source_ids.append(passed_record.source_id)
            elif failures:
                experience.verification = "none"
                experience.state = "contradicted"
                experience.polarity = "unknown"
                experience.causal = "stated"
                for failed_record, _failed in failures:
                    if failed_record.test_run_id and failed_record.test_run_id not in experience.counterexample_ids:
                        experience.counterexample_ids.append(failed_record.test_run_id)
    indexed: dict[str, list[ShortExperience]] = {}
    for record, experience in built:
        for token in (record.tool_call_id, record.patch_hash):
            if token:
                indexed.setdefault(token, []).append(experience)
    for record, experience in built:
        token = supersedes_token(record.content)
        if not token:
            continue
        for target in indexed.get(token, []):
            if target is not experience:
                target.state = "superseded"
                target.applies_as_current_solution = False
    for _record, experience in built:
        experience.applies_as_current_solution = bool(
            experience.state == "tested"
            and experience.verification == "verified"
            and experience.polarity == "success"
            and experience.applicability == "compatible"
        )
    return [experience for _record, experience in built]
