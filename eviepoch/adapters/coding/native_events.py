"""Normalize native client tool/session events into engineering fields."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ...domain import Source


@dataclass
class NativeEngineeringEvent:
    client: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    cwd: str | None = None
    repository: str | None = None
    commit_sha: str | None = None
    parent_shas: list[str] = field(default_factory=list)
    tool_input: str | None = None
    tool_output: str | None = None


def normalize_native_source(source: Source) -> NativeEngineeringEvent:
    event = NativeEngineeringEvent(session_id=source.session_id)
    locator = source.locator if isinstance(source.locator, dict) else {}
    for key, attr in (
        ("client", "client"),
        ("turn_id", "turn_id"),
        ("tool_call_id", "tool_call_id"),
        ("tool_name", "tool_name"),
        ("cwd", "cwd"),
        ("repository", "repository"),
        ("commit", "commit_sha"),
        ("client", "client"),
        ("tool_call_id", "tool_call_id"),
    ):
        value = locator.get(key)
        if isinstance(value, str) and value.strip():
            setattr(event, attr, value.strip())
    start = source.content.find("{")
    if start < 0:
        event.tool_output = source.content
        return event
    try:
        data = json.loads(source.content[start:])
    except (ValueError, TypeError):
        return event
    if not isinstance(data, dict):
        return event
    event.tool_name = event.tool_name or (data.get("tool") if isinstance(data.get("tool"), str) else None)
    event.cwd = event.cwd or (data.get("cwd") if isinstance(data.get("cwd"), str) else None)
    event.repository = event.repository or (data.get("repository") if isinstance(data.get("repository"), str) else None)
    if isinstance(data.get("commit"), str):
        event.commit_sha = data["commit"].lower()
    if isinstance(data.get("client"), str):
        event.client = data["client"]
    if isinstance(data.get("tool_call_id"), str):
        event.tool_call_id = data["tool_call_id"]
    parents = data.get("parents")
    if isinstance(parents, list):
        event.parent_shas = [str(item).lower() for item in parents if isinstance(item, str)]
    if isinstance(data.get("input"), str):
        event.tool_input = data["input"]
    elif data.get("input") is not None:
        event.tool_input = json.dumps(data["input"], ensure_ascii=False)
    if isinstance(data.get("output"), str):
        event.tool_output = data["output"]
    elif data.get("output") is not None:
        event.tool_output = json.dumps(data["output"], ensure_ascii=False)
    if event.tool_name and not locator.get("tool_name"):
        locator = dict(locator)
        locator["tool_name"] = event.tool_name
        if event.cwd:
            locator["cwd"] = event.cwd
        source.locator = locator
    return event
