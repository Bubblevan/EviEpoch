# Evidence packing replay: 2026-10-30

This change diagnoses API evidence loss and validation failures. It does not recompute an official score: the audit contains Add/Search payloads, not official final answers or per-question grades. Private payloads, model I/O, local databases and runtime credentials remain in ignored `logs/`.

## Observed triggers and controls

| Trigger | Behavior |
|---|---|
| A worked tool trajectory is reduced to its identifier | Same-domain opaque references can retrieve source-backed worked examples without requiring identical task IDs. Metadata-only excerpts fail a sufficiency check and receive at most one repair. Actions and observations remain original text. |
| A global data query receives selected table rows without their measurement context | Relevance-approved tabular units are retained for global analysis when the current query has no disclosure restriction. This retains only already-selected units and all output budgets still apply. |
| A quote copies display metadata or changes whitespace/escaping | Strip only the exact known display prefix when the remainder matches original bytes. Other invalid decisions receive one bounded model repair, followed by the same exact validators. No fuzzy matching or automatic whitespace rewriting. |
| Original record time is a human-readable wrapper | Parse supported `Time:` formats, preserve transport time separately, and use verified original-message time as the evidence time. Excerpts keep exact attribution headers except under a disclosure restriction. |
| A direct unknown attribute gets a related biography | Final support auditing distinguishes relevant values from topic similarity. Temporal inference is handled separately: duration and timestamp premises must not be removed for lacking a verbatim final year. |
| Relevant statements conflict | Keep validated supporting IDs even if the answer is unresolved. Epistemic uncertainty does not automatically become provider unavailability. Missing/invalid support IDs still fail validation. |
| Explicit document pages repeatedly fail neural list-cell extraction | Recognized document envelopes create source-exact navigation only, without generated facts or governance. Embedded document instructions remain data. Other inputs retain their existing extraction path. |

Disclosure decisions support `retain`, `excerpt`, and `exclude`. Full retention is prohibited under detected current-query disclosure restrictions. Restricted excerpts do not automatically add author/date metadata. A failed repair still fails closed. Quote validity is not a guarantee of semantic policy accuracy.

## Verification

- SQLite and PostgreSQL: **230 tests, zero failures/errors/skips**.
- Independent synthetic local HTTP smoke: **8 cases × 2 rounds = 16 successful content checks**, using real DeepSeek calls and text-embedding-v4. These checks cover retained operations/data, date/attribution context, privacy exclusions, unknown attributes, duration inference and unresolved conflicts. They are not official grades or new-task execution success.
- Final complete chronological API replay: **134 Add and 51 Search responses were HTTP 200**. Add used frozen successful extraction and stored vectors from an earlier live round; Search still made real model calls. A fresh database and original per-user event order preserve earlier/later memory states. Immutable private sources and namespace generations match the live Add run.
- Earlier live rounds and failures remain recorded. The second live run completed all 134 Add requests, with one Search 503. Freezing Add isolates subsequent Search changes and reduces repeated model cost; it does not constitute a third independent measurement of live Add reliability.

Public, content-free artifacts:

- [Offline tests and source hashes](../experiments/reports/smoke-packing-offline.json)
- [Independent synthetic HTTP checks](../experiments/reports/smoke-packing-public.json)
- [Replay aggregate metadata and mechanism checks](../experiments/reports/smoke-packing-replay-summary.json)

Private evidence was analyzed only for this debugging task. It was not added to public regression fixtures. The local replay and its provider differ from official Answer/Eval; neither HTTP success nor these checks imply that the official 60.76 score has increased.

## Metrics and release boundary

Source coverage and evidence sufficiency are separate. A fragment with a valid source ID does not establish complete evidence. `evidence_sufficiency` returns unknown for an arbitrary excerpt unless a whole-source return or declared public quote control establishes coverage. Proxy Answer checks remain separate.

`EPOCH_SUPPORT_AUDIT` controls direct-attribute support auditing. Existing privacy, proof binding, coverage and budget controls remain active. Complete live model quality, downstream task completion, sustained provider capacity and a new official run remain unverified. No production deployment is performed by this change.
