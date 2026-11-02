from __future__ import annotations

from ...domain import Source, annotate_span, locate, stable_id
from ..base import CodingArtifact
from .lexical import index_body
from .models import EngineeringObservation
from .parse_agent_xml import parse_agent_xml


def artifacts_from_claude_xml(source: Source) -> tuple[list[CodingArtifact], list]:
    spans = []
    artifacts: list[CodingArtifact] = []
    if "<observation>" not in source.content and "<summary>" not in source.content and "```xml" not in source.content:
        return artifacts, spans
    parsed = parse_agent_xml(source.content)
    if not parsed.valid:
        return artifacts, spans
    for index, obs in enumerate(parsed.observations):
        obs_id = stable_id("coding-obs-v1", source.id, "xml", index)
        try:
            anchor = obs.title or obs.narrative or (obs.facts[0] if obs.facts else source.content[:200])
            if anchor in source.content:
                span = locate(source, anchor)
            else:
                span = locate(source, source.content[: min(400, len(source.content))])
            spans.append(annotate_span(span))
        except Exception:
            pass
        observation = EngineeringObservation(
            obs_id,
            obs.type,  # type: ignore[arg-type]
            obs.title or obs.type,
            obs.facts or ([obs.narrative] if obs.narrative else []),
            obs.files_read,
            obs.files_modified,
            [],
            [],
            "proposed",
            [source.id],
        )
        body = index_body(observation.kind, observation.title, *observation.facts[:12],
                          *observation.files_modified, *observation.files_read)
        if obs.attachments:
            body = index_body(body, *obs.attachments)
        artifacts.append(
            CodingArtifact(
                obs_id,
                "coding_observation",
                observation.to_dict(),
                body,
                [source.id],
                source.asserted_at,
                index_body("observation", observation.kind, observation.title, *observation.facts[:6]),
            )
        )
    if parsed.summary and not parsed.summary.skipped and (parsed.summary.learned or parsed.summary.completed):
        summary = parsed.summary
        sid = stable_id("coding-summary-v1", source.id)
        artifacts.append(
            CodingArtifact(
                sid,
                "coding_observation",
                {
                    "observation_id": sid,
                    "kind": "discovery",
                    "title": summary.request or "session summary",
                    "facts": [part for part in (summary.learned, summary.completed, summary.next_steps) if part],
                    "state": "observed",
                    "source_ids": [source.id],
                },
                index_body(summary.request or "", summary.learned or "", summary.completed or ""),
                [source.id],
                source.asserted_at,
                index_body(summary.learned or "", summary.completed or ""),
            )
        )
    return artifacts, spans
