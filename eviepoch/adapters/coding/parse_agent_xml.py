"""Python execution of claude-mem v15 src/sdk/parser.ts (literature/reference @ v15.8.x)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_CODE_JSON = Path(__file__).with_name("reference") / "claude_mem" / "code.json"

OBSERVATION_TITLE_MAX_GRAPHEMES = 120
OBSERVATION_TITLE_TRUNCATE_AT = 117

OBSERVATION_SCHEMA_TAGS = {
    "type", "title", "subtitle", "narrative", "facts", "fact", "concepts", "concept",
    "files_read", "files_modified", "file", "attachments", "attachment",
}

ATTACHMENTS_BLOCK = re.compile(r"<attachments>([\s\S]*?)</attachments>")
ATTACHMENT_ELEMENT = re.compile(r"<attachment>([\s\S]*?)</attachment>", re.MULTILINE)
MAX_PARSED_ATTACHMENTS = 16
MAX_PARSED_ATTACHMENT_CHARS = 128

LABEL_WRAPPED_TITLE = re.compile(r"^\[\*\*title\*\*:\s*([\s\S]+?)\s*\]$")
XML_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


@dataclass
class ParsedObservation:
    type: str
    title: str | None
    subtitle: str | None
    facts: list[str] = field(default_factory=list)
    narrative: str | None = None
    concepts: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    attachments: list[str] | None = None


@dataclass
class ParsedSummary:
    request: str | None
    investigated: str | None
    learned: str | None
    completed: str | None
    next_steps: str | None
    notes: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class ParseResult:
    valid: bool
    observations: list[ParsedObservation] = field(default_factory=list)
    summary: ParsedSummary | None = None
    schema_drift: list[str] | None = None


def valid_observation_types() -> list[str]:
    data = json.loads(_CODE_JSON.read_text(encoding="utf-8"))
    return [item["id"] for item in data["observation_types"]]


def _strip_code_fences(text: str) -> str:
    match = re.match(r"^\s*```(?:xml)?\s*\n([\s\S]*?)\n```\s*$", text, re.IGNORECASE)
    return match.group(1) if match else text


def _decode_xml_references(value: str) -> str:
    def replace(reference: str) -> str:
        if reference.startswith("<![CDATA["):
            return reference
        body = reference[1:-1]
        if not body.startswith("#"):
            return XML_ENTITIES.get(body, reference)
        code_point = int(body[2:], 16) if body.startswith("#x") else int(body[1:])
        if code_point in (9, 10, 13) or 0x20 <= code_point <= 0xD7FF or 0xE000 <= code_point <= 0xFFFD or 0x10000 <= code_point <= 0x10FFFF:
            return chr(code_point)
        return reference

    return re.sub(
        r"<!\[CDATA\[[\s\S]*?\]\]>|&(?:amp|lt|gt|quot|apos|#(?:x[0-9a-fA-F]+|[0-9]+));",
        replace,
        value,
    )


def _extract_field(content: str, field_name: str) -> str | None:
    match = re.search(rf"<{field_name}>([\s\S]*?)</{field_name}>", content, re.IGNORECASE)
    if not match:
        return None
    trimmed = _decode_xml_references(match.group(1).strip())
    return None if trimmed.strip() == "" else trimmed


def _extract_type_field(content: str) -> str | None:
    match = re.search(r"<type>((?:(?!<type>)[\s\S])*?)</type>", content, re.IGNORECASE)
    if not match:
        return None
    value = _decode_xml_references(re.sub(r"</?[A-Za-z_][\w-]*\b[^>]*>", "", match.group(1))).strip()
    return None if value == "" else value


def _extract_array_elements(content: str, array_name: str, element_name: str) -> list[str]:
    elements: list[str] = []
    array_match = re.search(rf"<{array_name}>([\s\S]*?)</{array_name}>", content, re.IGNORECASE)
    if not array_match:
        return elements
    for element_match in re.finditer(rf"<{element_name}>([\s\S]*?)</{element_name}>", array_match.group(1), re.IGNORECASE):
        trimmed = _decode_xml_references(element_match.group(1).strip())
        if trimmed.strip():
            elements.append(trimmed)
    return elements


def _extract_attachment_labels(content: str) -> list[str] | None:
    block = ATTACHMENTS_BLOCK.search(content)
    if not block:
        return None
    labels: list[str] = []
    for element in ATTACHMENT_ELEMENT.finditer(block.group(1)):
        if len(labels) >= MAX_PARSED_ATTACHMENTS:
            break
        labels.append(element.group(1).strip()[:MAX_PARSED_ATTACHMENT_CHARS])
    return labels if labels else None


def _unwrap_label_wrapped_title(title: str | None) -> str | None:
    if title is None:
        return None
    match = LABEL_WRAPPED_TITLE.match(title)
    if not match:
        return title
    inner = match.group(1).strip()
    return title if inner == "" else inner


def _grapheme_len(text: str) -> int:
    return len(text)


def _extract_unstructured_observation_text(content: str) -> str | None:
    if re.search(r"</?(summary|skip_summary)\b", content, re.IGNORECASE):
        return None
    stripped = re.sub(
        r"<(type|title|subtitle|narrative|facts|concepts|files_read|files_modified|attachments)(?:\s*/>|>[\s\S]*?</\1>)",
        " ",
        content,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"<[^>]+>", " ", stripped).replace("\r\n", "\n")
    lines = [line.strip() for line in stripped.split("\n") if line.strip()]
    text = "\n".join(lines).strip()
    return None if text == "" else text


def _extract_observation_fallback(text: str) -> tuple[str, str | None]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    first_line = lines[0] if lines else text.strip()
    has_overflow = _grapheme_len(first_line) > OBSERVATION_TITLE_MAX_GRAPHEMES
    title = (
        first_line[:OBSERVATION_TITLE_TRUNCATE_AT] + "..."
        if has_overflow
        else first_line
    )
    narrative_lines = lines[1:] if not has_overflow else [first_line[OBSERVATION_TITLE_TRUNCATE_AT:].strip(), *lines[1:]]
    narrative = "\n".join(line for line in narrative_lines if line).strip()
    return title, narrative or None


def _parse_observation_blocks(text: str, schema_drift: set[str] | None = None) -> list[ParsedObservation]:
    observations: list[ParsedObservation] = []
    valid_types = valid_observation_types()
    fallback_type = valid_types[0]
    for match in re.finditer(r"<observation>([\s\S]*?)</observation>", text, re.IGNORECASE):
        obs_content = match.group(1)
        obs_type = _extract_type_field(obs_content)
        title = _unwrap_label_wrapped_title(_extract_field(obs_content, "title"))
        subtitle = _extract_field(obs_content, "subtitle")
        narrative = _extract_field(obs_content, "narrative")
        facts = _extract_array_elements(obs_content, "facts", "fact")
        concepts = _extract_array_elements(obs_content, "concepts", "concept")
        files_read = _extract_array_elements(obs_content, "files_read", "file")
        files_modified = _extract_array_elements(obs_content, "files_modified", "file")
        attachments = _extract_attachment_labels(obs_content)
        final_type = obs_type if obs_type else fallback_type
        cleaned_concepts = []
        for concept in concepts:
            colon = concept.find(":")
            trimmed = (concept if colon == -1 else concept[:colon]).strip()
            if trimmed and trimmed != final_type:
                cleaned_concepts.append(trimmed)
        final_title, final_narrative = title, narrative
        if not title and not narrative and not facts and not cleaned_concepts:
            salvage = _extract_unstructured_observation_text(obs_content)
            if not salvage:
                continue
            final_title, final_narrative = _extract_observation_fallback(salvage)
            if schema_drift is not None:
                for tag in re.finditer(r"</?([A-Za-z_][\w-]*)\b[^>]*>", obs_content):
                    name = tag.group(1).lower()
                    if name not in OBSERVATION_SCHEMA_TAGS:
                        schema_drift.add(name)
        observations.append(
            ParsedObservation(
                final_type,
                final_title,
                subtitle,
                facts,
                final_narrative,
                cleaned_concepts,
                files_read,
                files_modified,
                attachments,
            )
        )
    return observations


def _parse_summary_block(text: str) -> ParsedSummary | None:
    match = re.search(r"<summary>([\s\S]*?)</summary>", text, re.IGNORECASE)
    if not match:
        return None
    content = match.group(1)
    request = _extract_field(content, "request")
    investigated = _extract_field(content, "investigated")
    learned = _extract_field(content, "learned")
    completed = _extract_field(content, "completed")
    next_steps = _extract_field(content, "next_steps")
    notes = _extract_field(content, "notes")
    if not any((request, investigated, learned, completed, next_steps, notes)):
        return None
    return ParsedSummary(request, investigated, learned, completed, next_steps, notes)


def parse_agent_xml(raw: str, correlation_id: str | int | None = None) -> ParseResult:
    if not isinstance(raw, str) or not raw.strip():
        return ParseResult(valid=False)
    raw = _strip_code_fences(raw)
    skip_match = re.search(r'<skip_summary(?:\s+reason="([^"]*)")?\s*/>', raw, re.IGNORECASE)
    if skip_match:
        reason = skip_match.group(1)
        return ParseResult(
            valid=True,
            summary=ParsedSummary(None, None, None, None, None, None, True, _decode_xml_references(reason) if reason else None),
        )
    first_root = re.search(r"<(observation|summary)\b", raw, re.IGNORECASE)
    if not first_root:
        return ParseResult(valid=False)
    root = first_root.group(1).lower()
    if root == "observation":
        drift: set[str] = set()
        observations = _parse_observation_blocks(raw, drift)
        if not observations:
            return ParseResult(valid=False)
        return ParseResult(True, observations, None, sorted(drift) if drift else None)
    summary = _parse_summary_block(raw)
    if not summary:
        return ParseResult(valid=False)
    return ParseResult(True, [], summary)


def parse_observations(text: str, correlation_id: str | None = None) -> list[ParsedObservation]:
    result = parse_agent_xml(text, correlation_id)
    return result.observations if result.valid else []
