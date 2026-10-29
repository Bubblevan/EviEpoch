"""A final source-exact disclosure boundary; generated text cannot become evidence."""
from ..domain import locate
from ..providers import ProviderUnavailable


def disclose(query, plan, selected, snapshot, providers):
    review = getattr(providers, "disclose_evidence", None)
    if review is None or not selected:
        return selected, False
    data = review(query, plan, selected)
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise ProviderUnavailable("invalid disclosure decisions", stage="disclosure")
    available = {e.id: e for e in selected}
    found, result = set(), {}
    for row in data["decisions"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in available or row["id"] in found:
            raise ProviderUnavailable("invalid disclosure evidence ID", stage="disclosure")
        found.add(row["id"])
        quotes = row.get("quotes")
        if not isinstance(quotes, list) or len(quotes) > 8:
            raise ProviderUnavailable("invalid disclosure quotes", stage="disclosure")
        original = available[row["id"]]
        source = snapshot.sources[original.source_id]
        for quote in quotes:
            if not isinstance(quote, str) or not quote.strip() or quote not in original.content or quote not in source.content:
                raise ProviderUnavailable("disclosure quote is not selected source evidence", stage="disclosure")
            # Source-offset IDs distinguish repeated occurrences; the first exact one is stable.
            span = locate(source, quote, occurrence=0)
            from .engine import Evidence
            prefix = source.render()[:-len(source.content)] if source.content else ""
            result[span.id] = Evidence(span.id, prefix + quote, source.asserted_at, original.score, source.id)
    if found != available.keys():
        raise ProviderUnavailable("disclosure omitted a decision", stage="disclosure")
    return list(result.values()), True
