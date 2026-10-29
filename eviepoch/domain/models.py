"""Versioned memory contracts. Generated objects are navigation, never API evidence."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


class IdempotencyConflict(ValueError):
    pass


class IngestBusy(RuntimeError):
    pass


class InvalidExtraction(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def namespace(user_id: str) -> str:
    return digest(["namespace-v1", user_id])


def stable_id(*parts: Any) -> str:
    return digest(parts)[:32]


def timestamp(value: int | None) -> str | None:
    if value is None:
        return None
    # The official Textual adapter sends Unix milliseconds, including dates near the epoch.
    seconds = value / 1000
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("timestamp is outside the supported range") from exc


@dataclass
class TimeBounds:
    earliest: str | None = None
    latest: str | None = None
    precision: str = "unknown"
    expression: str = ""

    @classmethod
    def parse(cls, value: str | None) -> "TimeBounds":
        if value is None or value == "":
            return cls()
        if not isinstance(value, str):
            raise InvalidExtraction("time must be a string or null")
        if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
            raise InvalidExtraction("time must preserve year, month or day precision")
        parts = list(map(int, value.split("-")))
        year, month = parts[0], parts[1] if len(parts) > 1 else 1
        day = parts[2] if len(parts) > 2 else 1
        try:
            first = datetime(year, month, day)
            end_month = month if len(parts) > 1 else 12
            last_day = day if len(parts) > 2 else calendar.monthrange(year, end_month)[1]
            last = datetime(year, end_month, last_day)
        except ValueError as exc:
            raise InvalidExtraction("invalid calendar date") from exc
        return cls(first.date().isoformat(), last.date().isoformat(),
                   {1: "year", 2: "month", 3: "day"}[len(parts)], value)


@dataclass
class Source:
    id: str
    namespace: str
    request_id: str
    session_id: str
    ordinal: int
    role: str
    content: str
    asserted_at: str | None
    stored_at: str
    generation: int = 0
    locator: dict = field(default_factory=dict)
    timestamp_value: int | None = None

    def render(self) -> str:
        mark = f"[{self.asserted_at}]" if self.asserted_at else ""
        anchor = self.locator.get("temporal_anchor", {})
        if anchor.get("kind") == "original_message" and anchor.get("time") != self.asserted_at:
            mark += f"[original_message_time={anchor['time']}]"
        return f"{mark}[{self.role}] {self.content}"


@dataclass
class Span:
    id: str
    source_id: str
    byte_start: int
    byte_end: int
    text_hash: str
    quote: str


def locate(source: Source, quote: str, *, char_start=0, char_end=None, occurrence=None) -> Span:
    if not 0 <= char_start <= (char_end if char_end is not None else len(source.content)) <= len(source.content):
        raise InvalidExtraction("invalid extraction window")
    window = source.content[char_start:char_end]
    if not isinstance(quote, str) or not quote or quote not in window:
        raise InvalidExtraction("quote is absent from the authoritative source")
    positions, offset = [], 0
    while (position := window.find(quote, offset)) >= 0:
        positions.append(position)
        offset = position + len(quote)
    if occurrence is None and len(positions) != 1:
        raise InvalidExtraction("ambiguous quote; supply a larger unique quote")
    if occurrence is not None and (type(occurrence) is not int or not 0 <= occurrence < len(positions)):
        raise InvalidExtraction("invalid quote occurrence")
    start = char_start + positions[0 if occurrence is None else occurrence]
    byte_start = len(source.content[:start].encode("utf-8"))
    byte_end = byte_start + len(quote.encode("utf-8"))
    return Span(stable_id(source.id, byte_start, byte_end), source.id, byte_start, byte_end,
                hashlib.sha256(quote.encode("utf-8")).hexdigest(), quote)


@dataclass
class Node:
    id: str
    kind: str
    subject: str
    predicate: str
    value: str
    source_ids: list[str]
    span_ids: list[str]
    scope: str = "general"
    modality: str = "asserted"
    cardinality: str = "multi"
    change: str = "assertion"
    change_proposal: str = ""
    change_verified: bool = False
    change_targets: list[str] = field(default_factory=list)
    valid_time: TimeBounds = field(default_factory=TimeBounds)
    occurrence_time: TimeBounds = field(default_factory=TimeBounds)
    asserted_at: str | None = None
    tier: str = "hot"
    state: str = "hypothesis"
    protected: bool = False
    support_ids: list[str] = field(default_factory=list)
    counterexample_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0  # Evidence-support index, not a calibrated probability.
    generation: int = 0

    subject_id: str = ""
    predicate_id: str = ""
    value_id: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return ((self.subject_id or self.subject).casefold(),
                (self.predicate_id or self.predicate).casefold(), self.scope.casefold())

    def representation(self) -> str:
        return f"{self.kind}: {self.subject} {self.predicate} {self.value} [{self.scope}]"


@dataclass
class Edge:
    id: str
    origin: str
    target: str
    kind: str
    source_ids: list[str]
    derivation: str = "source_stated"


@dataclass
class Governance:
    action: str
    instruction_source_id: str
    source_ids: list[str]
    # Bounded by the committed generation; new Add sources may explicitly reintroduce knowledge.
    through_generation: int = 0
    subject: str = ""
    predicate: str = ""
    scope: str = ""


@dataclass
class Obligation:
    id: str
    query: str
    weight: float = 1.0
    # OR of AND proof sets. Sets contain source IDs; never generated answer text.
    alternatives: list[list[str]] = field(default_factory=list)


@dataclass
class QueryPlan:
    intents: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    as_of: TimeBounds = field(default_factory=TimeBounds)
    obligations: list[Obligation] = field(default_factory=list)
    required_relations: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    order_by: str = "event"


@dataclass
class Snapshot:
    generation: int
    policy_generation: int
    sources: dict[str, Source]
    nodes: dict[str, Node]
    edges: list[Edge]
    spans: dict[str, Span] = field(default_factory=dict)


def node_from_dict(value: dict) -> Node:
    value = dict(value)
    for key in ("valid_time", "occurrence_time"):
        value[key] = TimeBounds(**value.get(key, {}))
    return Node(**value)


def encode(value: Any) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":"))
