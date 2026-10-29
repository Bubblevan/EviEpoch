"""Source-exact, query-related windows with explicit incomplete-context markers."""
import re

from .lexical import terms

_CONSTRAINT = re.compile(r"\b(?:except|unless|never|must|only|not|before|after)\b|例外|除非|禁止|必须|不得|过敏", re.I)


def context_windows(text, query, max_chars=3200):
    if len(text) <= max_chars:
        return [{"start": 0, "end": len(text), "text": text}]
    query_terms = set(terms(query))
    lines, offset = [], 0
    for line in text.splitlines(keepends=True):
        lines.append((offset, offset + len(line), line))
        offset += len(line)
    if not lines:
        return []
    scores = [len(query_terms.intersection(terms(line))) for _, _, line in lines]
    relevance = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
    constraints = [i for i in relevance if _CONSTRAINT.search(lines[i][2])]
    # Reserve space for constraints even when the question doesn't name their vocabulary.
    seeds = list(dict.fromkeys(relevance[:3] + constraints[:3]))
    result, remaining = [], max_chars
    allowance = max(64, max_chars // max(1, len(seeds)))
    for i in seeds:
        start, end, line = lines[i]
        if end - start > allowance:
            matches = [m for m in re.finditer(r"\S+", line) if query_terms.intersection(terms(m.group()))]
            pivot = matches[0].start() if matches else 0
            constraints_in_line = list(_CONSTRAINT.finditer(line))
            if constraints_in_line and constraints_in_line[-1].start() - pivot > allowance:
                # JSON/escaped transcripts can be a single long line. Keep a distant
                # exception as its own window rather than taking only the line head.
                width = allowance // 2
                for point in (pivot, constraints_in_line[-1].start()):
                    left = lines[i][0] + max(0, point - width // 3)
                    right = min(lines[i][1], left + width)
                    if right-left <= remaining:
                        result.append({"start": left, "end": right, "text": text[left:right]})
                        remaining -= right-left
                continue
            start += max(0, pivot - allowance // 3)
            end = min(lines[i][1], start + allowance)
        else:
            if i > 0 and end - lines[i-1][0] <= allowance:
                start = lines[i-1][0]
            if i + 1 < len(lines) and lines[i+1][1] - start <= allowance:
                end = lines[i+1][1]
        if end - start <= remaining and not any(start < row["end"] and end > row["start"] for row in result):
            result.append({"start": start, "end": end, "text": text[start:end]})
            remaining -= end - start
    return sorted(result, key=lambda row: row["start"])
