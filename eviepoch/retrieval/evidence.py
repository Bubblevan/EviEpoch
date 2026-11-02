"""Evidence units and source-exact span packing."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain import Snapshot, stable_id
from ..domain.content import ContentPart

@dataclass
class Evidence:
    id: str
    content: str | list[ContentPart]
    created_at: str | None
    score: float
    source_id: str
    source_start: int = 0
    source_spans: tuple[str, ...] = ()
    source_ranges: tuple[tuple[int, int], ...] = ()
    source_prefix: str = ""
    source_paragraphs: tuple[int, ...] = ()


def _packed_source_items(ids: frozenset[str], evidence: dict[str, Evidence]) -> list[Evidence]:
    """Represent selected same-source spans as the single item returned to the caller."""
    by_source = {}
    for eid in ids:
        by_source.setdefault(evidence[eid].source_id, []).append(evidence[eid])
    packed = []
    for source_id, items in by_source.items():
        items.sort(key=lambda item: (item.source_start, item.id))
        prefix = items[0].source_prefix
        can_pack = (len(items) > 1 and prefix
                    and all(item.source_spans and item.source_prefix == prefix
                            for item in items))
        if not can_pack:
            packed.extend(items)
            continue
        spans = tuple(span_id for item in items for span_id in item.source_spans)
        ranges = tuple(source_range for item in items for source_range in item.source_ranges)
        content = prefix + "\n[…]\n".join(item.content[len(prefix):] for item in items)
        packed.append(Evidence(
            stable_id("multi-span-v1", source_id, spans), content,
            items[0].created_at, max(item.score for item in items), source_id,
            min(item.source_start for item in items), spans, ranges, prefix,
            tuple(paragraph for item in items for paragraph in item.source_paragraphs)))
    return packed


def _paragraph_at(source_content: str, byte_offset: int) -> int:
    """Return the one-based blank-line paragraph containing a UTF-8 byte offset."""
    prefix = source_content.encode("utf-8")[:byte_offset].decode("utf-8")
    return len(re.findall(r"\r?\n\s*\r?\n", prefix)) + 1


def _annotate_source_span(item: Evidence, snap: Snapshot) -> None:
    if item.source_ranges:
        return
    span = snap.spans.get(item.id)
    if span is not None and span.source_id == item.source_id:
        start, end = span.byte_start, span.byte_end
    elif item.id != item.source_id:
        source_text = snap.sources[item.source_id].content
        raw = source_text.encode("utf-8")
        start = item.source_start
        if not 0 <= start < len(raw):
            return
        char_start = len(raw[:start].decode("utf-8"))
        char_end_limit = min(len(source_text), char_start + len(item.content))
        end = 0
        for char_end in range(char_start + 1, char_end_limit + 1):
            quote = source_text[char_start:char_end]
            byte_end = len(source_text[:char_end].encode("utf-8"))
            if item.content.endswith(quote) and stable_id(item.source_id, start, byte_end) == item.id:
                end = byte_end
                break
        if not end:
            return
    else:
        return
    item.source_spans = (item.id,)
    item.source_ranges = ((start, end),)
    item.source_paragraphs = (_paragraph_at(snap.sources[item.source_id].content, start),)
