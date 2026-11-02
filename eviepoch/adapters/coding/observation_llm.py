from __future__ import annotations

import json
from datetime import datetime, timezone

from ...domain import Source


def build_observation_prompt(source: Source) -> str:
    tool_name = source.locator.get("tool_name") if isinstance(source.locator, dict) else None
    cwd = source.locator.get("cwd") if isinstance(source.locator, dict) else None
    occurred = source.asserted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return (
        f"<observed_from_primary_session>\n"
        f"  <what_happened>{tool_name or source.role}</what_happened>\n"
        f"  <occurred_at>{occurred}</occurred_at>\n"
        f"{f'  <working_directory>{cwd}</working_directory>\n' if cwd else ''}"
        f"  <parameters>{json.dumps(source.content[:12000], ensure_ascii=False)}</parameters>\n"
        f"</observed_from_primary_session>\n\n"
        "Return either one or more <observation>...</observation> blocks, or an empty response if this tool use should be skipped."
    )


def synthesize_observations(source: Source, providers) -> tuple[list, list]:
    if source.role not in {"tool", "assistant"} or not getattr(providers, "llm_enabled", False):
        return [], []
    completion = getattr(providers, "completion_text", None)
    if completion is None:
        return [], []
    try:
        xml_text = completion(
            "You emit claude-mem engineering observations as XML only, following reference/claude_mem/code.json.",
            build_observation_prompt(source),
            max_tokens=4000,
            stage="coding_observation_xml",
        )
    except Exception:
        return [], []
    from .claude_observations import artifacts_from_claude_xml

    synthetic = Source(
        source.id,
        source.namespace,
        source.request_id,
        source.session_id,
        source.ordinal,
        source.role,
        xml_text,
        source.asserted_at,
        source.stored_at,
        source.generation,
        source.locator,
    )
    return artifacts_from_claude_xml(synthetic)
