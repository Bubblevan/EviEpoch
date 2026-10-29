"""Numbered exact text units: the model selects references instead of copying bytes."""
import re
import json

_DISPLAY = re.compile(r"^(?:\[[^\]\n]{0,256}\])*\[(?:user|assistant|system|tool)\] ")
_ATTRIBUTION = re.compile(r"^(?:\[[^\]\n]{1,128}\]\s*)*(?:[A-Za-z][A-Za-z .#'’\-]{0,64}|[\u3400-\u9fff]{1,12}):\s*")


def disclosure_units(evidence):
    content = evidence.content
    header = _DISPLAY.match(content)
    if header:
        content = content[header.end():]
    start = content.find('{')
    if 0 <= start <= 256:
        try:
            structured = json.loads(content[start:])
        except (TypeError, ValueError):
            structured = None
        if isinstance(structured, dict):
            # Subject/relation/object and update directives form one semantic unit.
            # Splitting JSON commas can detach a value from its entity/version.
            return [{"id": "u0", "text": content}]
    units = []
    attribution = _ATTRIBUTION.match(content)
    if attribution:
        units.append({"id": "u0", "text": content[:attribution.end()]})
        content = content[attribution.end():]
    # Sentence/newline boundaries preserve punctuation, escapes and whitespace.
    # A very long serialized line uses bounded contiguous windows, not decoding.
    for match in re.finditer(r".+?(?:\r?\n|(?<=[.!?。！？,，;；])(?=\s)|$)", content, re.S):
        text = match.group()
        for start in range(0, len(text), 600):
            piece = text[start:start+600]
            if piece.strip():
                units.append({"id": f"u{len(units)}", "text": piece})
    return units


def disclosure_input(evidence):
    return {"id": evidence.id, "created_at": evidence.created_at,
            "source_id": evidence.source_id, "units": disclosure_units(evidence)}
