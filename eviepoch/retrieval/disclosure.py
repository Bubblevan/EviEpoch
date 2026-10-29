"""A final source-exact disclosure boundary; generated text cannot become evidence."""
from ..domain import locate
from ..providers import ProviderUnavailable
from .context import (attribution_prefix, has_disclosure_restriction, metadata_only_quotes,
                      tabular_observations, worked_example_for_reference)


def disclose(query, plan, selected, snapshot, providers):
    review = getattr(providers, "disclose_evidence", None)
    if review is None or not selected:
        return selected, False
    data = review(query, plan, selected)
    try:
        return _validate(query, plan, selected, snapshot, data)
    except ProviderUnavailable as error:
        repair = getattr(providers, "repair_disclosure", None)
        if repair is None:
            raise
        revised = repair(query, plan, selected, data, str(error))
        # Exactly one model repair, with the same units and all original validators.
        return _validate(query, plan, selected, snapshot, revised)


def _validate(query, plan, selected, snapshot, data):
    available = {e.id: e for e in selected}
    def lost_procedure(value):
        if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
            return False
        for row in value["decisions"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in available:
                continue
            original = available[row["id"]]
            if (row.get("mode", "excerpt") == "excerpt" and metadata_only_quotes(row.get("quotes", []))
                    and worked_example_for_reference(query, snapshot.sources[original.source_id])):
                return True
        return False
    if lost_procedure(data):
        raise ProviderUnavailable("disclosure removed the worked procedure", stage="disclosure_sufficiency")
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise ProviderUnavailable("invalid disclosure decisions", stage="disclosure")
    found, result = set(), {}
    for row in data["decisions"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in available or row["id"] in found:
            raise ProviderUnavailable("invalid disclosure evidence ID", stage="disclosure")
        found.add(row["id"])
        mode = row.get("mode", "excerpt")
        if not isinstance(mode, str) or mode not in {"retain", "excerpt", "exclude"}:
            raise ProviderUnavailable("invalid disclosure mode", stage="disclosure")
        quotes = row.get("quotes")
        if not isinstance(quotes, list) or len(quotes) > 8:
            raise ProviderUnavailable("invalid disclosure quotes", stage="disclosure")
        original = available[row["id"]]
        source = snapshot.sources[original.source_id]
        if mode == "retain":
            if quotes or has_disclosure_restriction(query):
                raise ProviderUnavailable("full retention is invalid under a disclosure restriction", stage="disclosure")
            # The reviewer approves the already-selected unit. No new source, decoding,
            # substring expansion or budget exemption is granted by this decision.
            result[original.id] = original
            continue
        if mode == "exclude":
            if quotes:
                raise ProviderUnavailable("excluded evidence contains quotes", stage="disclosure")
            continue
        row_spans = []
        for quote in quotes:
            display_prefix = source.render()[:-len(source.content)] if source.content else ""
            if (isinstance(quote, str) and display_prefix and quote.startswith(display_prefix)
                    and quote not in source.content
                    and quote[len(display_prefix):] and quote[len(display_prefix):] in source.content
                    and quote[len(display_prefix):] in original.content):
                # The model sometimes copies the exact supplied display envelope.
                # Strip only this known prefix and still require exact source bytes.
                quote = quote[len(display_prefix):]
            if not isinstance(quote, str) or not quote.strip() or quote not in original.content or quote not in source.content:
                # A model may accidentally include one unmatched closing quote as part of the excerpt.
                # Recover only when removing that single delimiter yields an exact substring of both
                # the selected evidence and its authoritative source; never fuzzy-match or rewrite text.
                if (isinstance(quote, str) and len(quote) > 8 and quote[-1] in {'"', "'", "”", "’"}
                        and quote[:-1] in original.content and quote[:-1] in source.content):
                    quote = quote[:-1]
                else:
                    raise ProviderUnavailable("disclosure quote is not selected source evidence", stage="disclosure")
            # Source-offset IDs distinguish repeated occurrences; the first exact one is stable.
            span = locate(source, quote, occurrence=0)
            from .engine import Evidence
            restricted = has_disclosure_restriction(query)
            prefix = f"[{source.role}] " if restricted else source.render()[:-len(source.content)]
            attribution = "" if restricted else attribution_prefix(source)
            result[span.id] = Evidence(span.id, prefix + attribution + quote,
                                       None if restricted else source.evidence_time, original.score, source.id)
            row_spans.append(span.id)
        if (row_spans and "global" in plan.intents and not has_disclosure_restriction(query)
                and tabular_observations(original.content)):
            # A relevance-approved measurement unit must keep all observations for
            # aggregate inference; selecting a few rows can change the trend. This
            # retains only the existing selected unit, still under final budgets.
            for eid in row_spans:
                result.pop(eid, None)
            result[original.id] = original
    if found != available.keys():
        raise ProviderUnavailable("disclosure omitted a decision", stage="disclosure")
    return list(result.values()), True
