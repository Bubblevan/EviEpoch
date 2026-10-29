"""Small, source-exact attribution headers and explicit query disclosure restrictions."""
import re
import json

_RESTRICTION = re.compile(r"policy\s+compliance|disclosure\s+polic|do\s+not\s+(?:disclose|reveal|share)|"
                          r"don't\s+(?:disclose|reveal|share)|must\s+not\s+(?:disclose|reveal)|"
                          r"do\s+not\s+include|don't\s+include|only\s+(?:disclose|reveal|share)|"
                          r"without\s+(?:revealing|disclosing)|"
                          r"不得(?:披露|透露|公开)|禁止(?:披露|透露|公开)|不要(?:披露|透露|公开|包含)|隐去|匿名|只披露", re.I)


def has_disclosure_restriction(query):
    return bool(_RESTRICTION.search(query))


def attribution_prefix(source):
    # Only the leading record wrapper / named speaker, never the neighboring biography.
    text = source.content
    match = re.match(r"(?P<header>(?:\[[^\]\n]{1,128}\]\s*)*(?:[A-Za-z][A-Za-z .#'’\-]{0,64}|[\u3400-\u9fff]{1,12}):)\s*", text)
    return "[source_attribution=" + match.group("header") + "] " if match else ""


def worked_example_for_reference(query, source):
    reference = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9_-]{0,31})\s+task\s*(?:index|id)\s*[:=#]?\s*\d+\s*", query, re.I)
    if reference is None:
        return False
    start = source.content.find("{")
    if start < 0 or start > 256:
        return False
    try:
        record = json.loads(source.content[start:])
    except (TypeError, ValueError):
        return False
    return (isinstance(record, dict) and isinstance(record.get("dataset"), str)
            and record["dataset"].casefold() == reference.group(1).casefold()
            and isinstance(record.get("trajectory"), list) and bool(record["trajectory"]))


def metadata_only_quotes(quotes):
    return bool(quotes) and all(isinstance(q, str) and re.fullmatch(
        r'\s*["\']?(?:task_index|dataset|schema_version)["\']?\s*:\s*["\']?[A-Za-z0-9_.-]+["\']?\s*,?\s*', q)
                               for q in quotes)


def tabular_observations(text):
    # Decode separators only for classification. Evidence remains byte-for-byte raw.
    readable = text.replace(r"\n", "\n").replace(r"\t", "\t")
    rows = 0
    for line in readable.splitlines():
        cells = re.split(r"\t|,|\|", line)
        numeric = sum(bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?", cell.strip())) for cell in cells)
        if len(cells) >= 2 and numeric >= 2:
            rows += 1
        if rows >= 3:
            return True
    return False


def temporal_value_request(query, plan):
    text = query + " " + " ".join(plan.attributes)
    text = text.replace("_", " ")
    return bool(re.search(r"\b(?:when|date|year|years|duration|interval|age|elapsed)\b|"
                          r"how\s+long|什么时候|哪一年|哪年|多久|日期|间隔|时长|年龄", text, re.I))
