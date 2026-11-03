from __future__ import annotations

import json
import re
from pathlib import Path

from ...domain import Source, stable_id
from ..base import CodingArtifact
from .lexical import index_body
from .models import DecisionOutcome

_PROMPT_RS = Path(__file__).with_name("reference") / "causal_memory" / "reasoning_extractor.rs"
_JSON_ARRAY = re.compile(r"\[[\s\S]*\]")


def _reasoning_prompt() -> str:
    text = _PROMPT_RS.read_text(encoding="utf-8")
    match = re.search(r'const REASONING_EXTRACT_PROMPT: &str = r#"(.*?)"#;', text, re.DOTALL)
    if not match:
        raise RuntimeError("reasoning prompt missing from reference/causal_memory/reasoning_extractor.rs")
    return match.group(1)


def extract_reasoning_decisions(source: Source, providers) -> list[CodingArtifact]:
    if source.role != "assistant" or len(source.content) < 100:
        return []
    extract = getattr(providers, "extract_json", None)
    if extract is None:
        return []
    try:
        raw = extract(_reasoning_prompt(), source.content[:8000])
    except Exception:
        return []
    if isinstance(raw, str):
        match = _JSON_ARRAY.search(raw)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return []
    elif isinstance(raw, list):
        data = raw
    else:
        return []
    artifacts: list[CodingArtifact] = []
    for index, item in enumerate(data[:3]):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision")
        reasoning = item.get("reasoning", "")
        if not isinstance(decision, str) or not decision.strip():
            continue
        outcome_id = stable_id("coding-outcome-v1", source.id, index, decision[:64])
        outcome = DecisionOutcome(
            outcome_id,
            decision.strip(),
            reasoning if isinstance(reasoning, str) and reasoning.strip() else decision.strip(),
            [],
            "unknown",
            [],
            [],
            [],
            [],
            "hypothesized",
            [source.id],
            premises=[reasoning.strip()] if isinstance(reasoning, str) and reasoning.strip() else [],
        )
        artifacts.append(
            CodingArtifact(
                outcome_id,
                "coding_decision_outcome",
                outcome.to_dict(),
                index_body(outcome.problem, outcome.decision),
                [source.id],
                source.asserted_at,
                index_body("decision", outcome.decision, outcome.problem),
            )
        )
    return artifacts
