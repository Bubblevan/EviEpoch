"""A final source-exact disclosure boundary; generated text cannot become evidence."""
from ..domain import locate
from ..providers import ProviderUnavailable
from .context import (attribution_prefix, has_disclosure_restriction, metadata_only_quotes,
                      tabular_observations, worked_example_for_reference)
from .disclosure_units import disclosure_units


def pack_excerpts(query, evidence, snapshot, limit):
    """Fit source-backed disjoint excerpts without dropping complementary premises.

    Only when fragments overflow the item limit, group same-source quotes with an
    explicit omission marker. No intervening source bytes are filled in.
    """
    if len(evidence) <= limit:
        return evidence
    from ..domain import stable_id
    from .engine import Evidence
    groups = {}
    for item in evidence:
        groups.setdefault(item.source_id, []).append(item)
    result = []
    for sid, group in groups.items():
        if len(group) == 1:
            result.extend(group)
            continue
        source = snapshot.sources[sid]
        restricted = has_disclosure_restriction(query)
        prefix = f"[{source.role}] " if restricted else source.render()[:-len(source.content)]
        prefix += "" if restricted else attribution_prefix(source)
        quotes, span_ids, ranges = [], [], []
        for item in sorted(group, key=lambda e: (e.source_start, e.id)):
            if not item.content.startswith(prefix):
                raise ProviderUnavailable("cannot pack an unvalidated evidence envelope", stage="disclosure")
            quote = item.content[len(prefix):]
            span = locate(source, quote, occurrence=0)
            if span.id != item.id:
                raise ProviderUnavailable("cannot pack an unvalidated source span", stage="disclosure")
            quotes.append(quote)
            span_ids.append(span.id)
            ranges.append((span.byte_start, span.byte_end))
        result.append(Evidence(stable_id('disclosure-bundle-v1', sid, span_ids),
                               prefix + '\n[…]\n'.join(quotes), None if restricted else source.evidence_time,
                               max(e.score for e in group), sid, min(e.source_start for e in group), tuple(span_ids), tuple(ranges)))
    return result


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
            units = {u['id']: u['text'] for u in disclosure_units(original)}
            selected_units = row.get('unit_ids', [])
            quoted = row.get('quotes', [])
            if isinstance(selected_units, list) and isinstance(quoted, list):
                quoted = quoted or [units[uid] for uid in selected_units if isinstance(uid, str) and uid in units]
            if (row.get("mode", "excerpt") == "excerpt" and metadata_only_quotes(quoted)
                    and worked_example_for_reference(query, snapshot.sources[original.source_id])):
                return True
            if (row.get('mode') == 'exclude' and not has_disclosure_restriction(query)
                    and worked_example_for_reference(query, snapshot.sources[original.source_id])):
                return True
        return False
    if lost_procedure(data):
        raise ProviderUnavailable("disclosure removed the same-domain procedure: an opaque task adapter requests transferable experience, including failed actions and feedback, rather than an identical task number", stage="disclosure_sufficiency")
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
        quotes = row.get("quotes", [])
        if not isinstance(quotes, list) or len(quotes) > 8:
            raise ProviderUnavailable("invalid disclosure quotes", stage="disclosure")
        original = available[row["id"]]
        source = snapshot.sources[original.source_id]
        unit_ids = row.get("unit_ids", [])
        units = {u['id']: u['text'] for u in disclosure_units(original)}
        if (not isinstance(unit_ids, list) or len(unit_ids) > 128
                or any(not isinstance(uid, str) or uid not in units for uid in unit_ids)
                or len(set(unit_ids)) != len(unit_ids)):
            raise ProviderUnavailable("invalid disclosure unit IDs", stage="disclosure")
        if mode == "retain":
            if quotes or unit_ids or has_disclosure_restriction(query):
                raise ProviderUnavailable("full retention is invalid under a disclosure restriction", stage="disclosure")
            # The reviewer approves the already-selected unit. No new source, decoding,
            # substring expansion or budget exemption is granted by this decision.
            result[original.id] = original
            continue
        if mode == "exclude":
            if quotes or unit_ids:
                raise ProviderUnavailable("excluded evidence contains quotes", stage="disclosure")
            continue
        row_spans = []
        exact_spans = []
        selected_units = [units[uid] for uid in unit_ids]
        parent_runs, previous_index = [], -2
        for index, unit in enumerate(disclosure_units(original)):
            if unit['id'] not in unit_ids:
                continue
            if index == previous_index + 1:
                parent_runs[-1] += unit['text']
            else:
                parent_runs.append(unit['text'])
            previous_index = index
        if quotes and selected_units and any(not isinstance(q, str) or not any(q in parent for parent in parent_runs) for q in quotes):
            raise ProviderUnavailable("quote refinement is outside selected disclosure units", stage="disclosure")
        # When both are given, exact quotes NARROW the parent units. Never union
        # a permissible refinement with its potentially sensitive surrounding text.
        for quote in quotes or selected_units:
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
                                       None if restricted else source.evidence_time, original.score, source.id,
                                       source_start=span.byte_start)
            row_spans.append(span.id)
            exact_spans.append(span)
        if len(exact_spans) > 1:
            intervals = []
            for span in sorted(exact_spans, key=lambda s: (s.byte_start, s.byte_end)):
                if intervals and span.byte_start <= intervals[-1][1]:
                    intervals[-1][1] = max(intervals[-1][1], span.byte_end)
                else:
                    intervals.append([span.byte_start, span.byte_end])
            # Rejoin only overlapping/adjacent approved bytes. No omitted middle
            # clause is reintroduced, including under disclosure restrictions.
            if len(intervals) < len(exact_spans):
                for eid in row_spans:
                    result.pop(eid, None)
                row_spans = []
                raw = source.content.encode('utf-8')
                for start, end in intervals:
                    quote = raw[start:end].decode('utf-8')
                    span = locate(source, quote, occurrence=0)
                    result[span.id] = Evidence(span.id, prefix + attribution + quote,
                                               None if restricted else source.evidence_time, original.score, source.id,
                                               source_start=start)
                    row_spans.append(span.id)
        if (row_spans and "global" in plan.intents and not has_disclosure_restriction(query)
                and tabular_observations(original.content)):
            # A relevance-approved measurement unit must keep all observations for
            # aggregate inference; selecting a few rows can change the trend. This
            # retains only the existing selected unit, still under final budgets.
            for eid in row_spans:
                result.pop(eid, None)
            result[original.id] = original
        elif row_spans and not has_disclosure_restriction(query):
            # If approved bytes already cover the complete source, keep its stable
            # source ID; this grants no expansion of text or display metadata.
            whole = locate(source, source.content, occurrence=0)
            if row_spans == [whole.id] and original.id == source.id:
                result.pop(whole.id, None)
                result[original.id] = original
    if found != available.keys():
        raise ProviderUnavailable("disclosure omitted a decision", stage="disclosure")
    return list(result.values()), True
