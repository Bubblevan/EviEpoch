"""Versioned memory contracts. Generated objects are navigation, never API evidence."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 4


class IdempotencyConflict(ValueError):
    pass


class IngestBusy(RuntimeError):
    pass


class PublishConflict(RuntimeError):
    """The namespace changed while derived objects were being embedded."""


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
        from ..ingest.temporal import anchor_for
        anchor = anchor_for(self)
        primary = anchor.get("time") if anchor.get("kind") == "original_message" else self.asserted_at
        mark = f"[{primary}]" if primary else ""
        if anchor.get("kind") == "original_message" and anchor.get("time") != self.asserted_at:
            mark += f"[original_message_time={anchor['time']}]"
            if self.asserted_at:
                mark += f"[transport_time={self.asserted_at}]"
        return f"{mark}[{self.role}] {self.content}"

    @property
    def evidence_time(self):
        from ..ingest.temporal import anchor_for
        anchor = anchor_for(self)
        return anchor.get("time") if anchor.get("kind") == "original_message" else self.asserted_at


@dataclass
class Span:
    id: str
    source_id: str
    byte_start: int
    byte_end: int
    text_hash: str
    quote: str
    temporal_start: str | None = None
    signal_tags: list[str] = field(default_factory=list)
    date_mentions: list[str] = field(default_factory=list)


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


_MENTIONED_DATE = re.compile(r"(?<!\d)(\d{4})(?:[-/.年](\d{1,2})(?:[-/.月](\d{1,2})日?)?)?(?!\d)")
_NEGATION_SIGNAL = re.compile(r"\b(?:not|never|no|don't|doesn't|didn't|cannot|can't|without)\b|否定|不|没|無|無法|不要|从不|從不", re.I)
_EXCEPTION_SIGNAL = re.compile(r"\b(?:except|unless|exception|excluding|but only|only if)\b|例外|除非|但仅|但僅|除外", re.I)
_NUMBER_SIGNAL = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*(?:%|％|\s*(?:kg|g|mg|km|m|cm|mm|years?|months?|days?|小时|小時|天|元|美元|件|次))?", re.I)


def date_mentions(text: str) -> list[str]:
    dates = []
    for year, month, day in _MENTIONED_DATE.findall(text):
        value = year
        if month:
            value += f"-{int(month):02d}"
        if day:
            value += f"-{int(day):02d}"
        try:
            datetime.fromisoformat(value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else ""))
        except ValueError:
            continue
        # The date is an index hint for source mention time, not a claim about validity.
        if len(value) == 4:
            value += "-01-01"
        elif len(value) == 7:
            value += "-01"
        dates.append(value)
    return list(dict.fromkeys(dates))


def annotate_span(span: Span) -> Span:
    span.date_mentions = date_mentions(span.quote)
    span.temporal_start = span.date_mentions[0] if span.date_mentions else None
    span.signal_tags = signal_tags(span.quote)
    return span


def signal_tags(text: str) -> list[str]:
    tags = []
    if _NEGATION_SIGNAL.search(text):
        tags.append("negation")
    if _EXCEPTION_SIGNAL.search(text):
        tags.append("exception")
    if _NUMBER_SIGNAL.search(text):
        tags.append("numeric")
    return tags


def chunk_source(source: Source, max_bytes: int = 1800) -> list[Span]:
    """Create byte-exact, independently indexable passage spans for long sources."""
    if not 256 <= max_bytes <= 2048:
        raise ValueError("source chunk size must be between 256 and 2048 UTF-8 bytes")
    text = source.content
    if len(text.encode("utf-8")) <= max_bytes:
        return []
    spans = []
    start = 0
    while start < len(text):
        end = start
        size = 0
        while end < len(text):
            width = len(text[end].encode("utf-8"))
            if size + width > max_bytes:
                break
            size += width
            end += 1
        if end == start:
            end += 1
        if end < len(text):
            # Prefer a paragraph/sentence boundary near the end of the byte budget.
            floor = max(start + 1, end - max(32, (end - start) // 4))
            boundary = max((text.rfind(mark, floor, end) for mark in ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ")))
            if boundary >= floor:
                end = boundary + (2 if text[boundary:boundary + 2] in {"\n\n", ". ", "! ", "? "} else 1)
        quote = text[start:end]
        byte_start = len(text[:start].encode("utf-8"))
        byte_end = byte_start + len(quote.encode("utf-8"))
        span = Span(stable_id(source.id, byte_start, byte_end), source.id, byte_start, byte_end,
                    hashlib.sha256(quote.encode("utf-8")).hexdigest(), quote)
        spans.append(annotate_span(span))
        start = end
    return spans


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
