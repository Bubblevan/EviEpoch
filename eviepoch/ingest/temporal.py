"""Conservative, source-backed original-message anchors and relative date bounds."""
from datetime import date, datetime, timedelta
import re

from ..domain import TimeBounds

_ISO = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"


def message_anchor(source):
    text = source.content
    candidates = []
    # Recognized record wrappers only: a random date in prose is not a chat timestamp.
    for match in re.finditer(r"\[Time:\s*(" + _ISO + r")\]", text):
        candidates.append((match.group(1), match.group(0)))
    if re.match(r"^(?:\[[^\]\n]+\]){2,}", text):
        match = re.search(r"\n(" + _ISO + r")\s*$", text)
        if match:
            candidates.append((match.group(1), match.group(1)))
    valid = []
    for value, quote in candidates:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        valid.append((parsed.isoformat(), quote))
    distinct = {value for value, _ in valid}
    if len(distinct) == 1:
        value, quote = valid[0]
        return {"time": value, "kind": "original_message", "quote": quote, "ambiguous": False}
    if len(distinct) > 1:
        return {"time": None, "kind": "conflicting_original_messages", "quote": "", "ambiguous": True}
    return {"time": source.asserted_at, "kind": "transport", "quote": "", "ambiguous": False}


def anchor_for(source):
    return source.locator.get("temporal_anchor", {"time": source.asserted_at, "kind": "transport",
                                                 "quote": "", "ambiguous": False})


def relative_bounds(expression, anchor):
    if not anchor or not isinstance(expression, str):
        return None
    try:
        base = date.fromisoformat(anchor[:10])
    except (ValueError, TypeError):
        return None
    lowered = expression.casefold()
    offsets = []
    for pattern, offset in [(r"\byesterday\b|昨天|昨日", -1), (r"\btoday\b|今天|今日", 0),
                            (r"\btomorrow\b|明天", 1), (r"前天", -2)]:
        if re.search(pattern, lowered):
            offsets.append(offset)
    match = re.search(r"\b(\d{1,4})\s+days?\s+ago\b|(\d{1,4})\s*天前", lowered)
    if match:
        offsets.append(-int(match.group(1) or match.group(2)))
    if len(set(offsets)) == 1:
        try:
            value = (base + timedelta(days=offsets[0])).isoformat()
        except OverflowError:
            return None
        return TimeBounds(value, value, "day", expression)
    if not offsets and re.search(r"半个?月前|\b(?:about\s+)?(?:half a month|two weeks) ago\b", lowered):
        return TimeBounds((base - timedelta(days=20)).isoformat(), (base - timedelta(days=10)).isoformat(),
                          "approximate", expression)
    return None
