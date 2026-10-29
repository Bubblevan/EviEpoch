"""Source-level metrics for evidence outputs that may expose disclosed span IDs."""
from __future__ import annotations


def evidence_metrics(groups: list[set[str]], returned_sources: set[str]):
    """Return complete SOURCE coverage and recall; excerpts may still omit every premise."""
    if not groups:
        return None, None
    complete = any(group <= returned_sources for group in groups)
    recall = max((len(group & returned_sources) / len(group) for group in groups if group), default=None)
    return complete, recall


def evidence_sufficiency(groups, returned_sources, response, required_quotes=()):
    """Do not turn arbitrary fragments into a complete proof merely through source IDs."""
    source_complete, _ = evidence_metrics(groups, returned_sources)
    if source_complete is None or source_complete is False:
        return source_complete
    if required_quotes:
        contents = [item.get("content", "") for item in response]
        return all(any(quote in text for text in contents) for quote in required_quotes)
    whole_ids = {item["id"] for item in response}
    return True if any(group <= whole_ids for group in groups) else None
