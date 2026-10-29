# Answer-level replay and evidence assembly, 2026-10-30

This records the `c23bd01` iteration and its tested source snapshot. Subsequent research can use the explicitly requested [Codex CLI Luna backend](CODEX_LUNA_RESEARCH.md); its new validation does not rewrite the DeepSeek results below.

This iteration uses the official capability breakdown to prioritize diagnosis, not to invent private per-question labels. The available API log contains Add/Search, not the platform's final Answer/Eval output. Local LLM reviews are fallible diagnostic evidence, never an official score.

## Verified failure mechanisms and changes

| Mechanism | Change and boundary |
|---|---|
| Personalized advice searches for a prewritten recommendation instead of relevant user constraints | Planning, binding and auditing admit source-backed preferences and limitations as premises. No new personal facts or recommendations are persisted. |
| Follow-up questions omit topic words and disappear from discussion-order evidence | Bounded active turns from the same Add/session enter candidate verification. Questions establish what was discussed, not the truth of their premises. This is a limited adaptation of ReFind's chat-native context, not a reproduction of its agent. |
| Broad background satisfies a proof alternative while a specific stated value is omitted | The binder may identify a minimal set of direct source premises in `preferred_sources`; every ID is validated in the live snapshot and still passes final disclosure and budgets. This adapts MemGuide's missing-slot focus. |
| The model copies long, escaped or multilingual excerpts incorrectly | Prefer numbered exact source units. Quoted refinements can only narrow selected units. Unknown IDs, unsupported text, and prohibited whole retention still fail validation; one bounded repair remains. |
| Fine units detach a value, correction or update from its context | Keep structured JSON records intact. Rejoin only adjacent/overlapping approved bytes; never restore omitted intervening content. Preserve original source offsets and order. |
| `top_k` is consumed by fragments of a few sources, dropping complementary sources | If disclosed fragments overflow the item count, pack same-source excerpts into one item with an explicit omission marker. Trace source/span IDs and byte ranges. Final item, character and token budgets still apply. |
| Model output is truncated by a fixed small ID-list budget | Allocate bounded audit/verifier output space from source count, and proof-binding output space from obligation count. Truncated responses remain invalid. |
| A relation enum appears in query intents | Validate the plan and request one repair with explicit legal and illegal intent values; validate the repaired plan again. No silent alias conversion. |
| Unresolved answers are confused with unavailable evidence | Audited, nonempty, valid partial source IDs can survive an `uncertain` fallback, explicitly marked as partial. Uncertainty without valid supporting IDs remains a failure. |
| A different-numbered or failed trajectory is silently excluded | Typed same-domain task references request experience, including failed actions and feedback. Reject silent removal and request one bounded repair. This does not claim that the old trajectory solves the target environment. |

The literature connections are research hypotheses drawn from the local [ReFind](../literature/cards/10-refind.md), [MemGuide](../literature/cards/08-memguide.md), [MemoTime](../literature/cards/05-memotime.md), [MemWeaver](../literature/cards/06-memweaver.md) and [PreCoMem](../literature/cards/02-precomem.md) notes. No upstream code is copied in this change. No paper's benchmark score is transferred to this system. Temporal premises, source-backed state, and completeness constraints are evaluated separately from parsing and assembly fixes.

## What was measured

- Final code: 272 SQLite/PostgreSQL tests pass with zero failures/errors/skips; compileall and diff checks pass.
- The last complete live-Search chronological replay (`r12`) returned 134 Add and 51 Search HTTP 200 responses. Add reused the earlier successful extraction and vectors; Search made real DeepSeek calls. This is not a fresh live-Add reliability measurement.
- Its 260 evidence items passed exact-byte source/span checks, namespace checks and memory-generation checks. This validates provenance, not answer accuracy. Packing behavior also has independent restricted-data tests.
- The original 21 synthetic questions achieved a full strict local pass in the seventh control iteration: answers 21/21, no measured leakage, all declared source checks passed. Evidence sufficiency was directly scored on 17 questions and unknown on four. Do not call the unknown cases complete.
- Earlier nine-question context-control iterations achieved answers 9/9 and the then-declared checks. The suite subsequently corrected a non-minimal evidence constraint: a current update explicitly carries forward approval and deadline status, so policy plus update is a valid alternative to policy plus the old state plus update. The reference answer did not change; this is an oracle correction, not an algorithm gain.
- Not every iteration passed. A prototype reached 21/21 answers while exposing irrelevant personal details; another consumed item limits with fragmented measurements. Both failures are retained and led to the fixes above.
- Private paired Answer/raw-context reviews exposed genuine omissions and reviewer errors. A model treated a later calendar date as non-current, ignored explicit task-local updates, and sometimes asserted a retrieval gap while its own explanation said the fact was absent from raw memory. Unsupported/disputed labels remain unresolved. Opaque external tasks need their execution environment, which is absent from this log.

## Current blocking condition

The final candidate (`r13`) did **not** complete real-provider acceptance. It returned 134 Add HTTP 200 responses using frozen Add, 45 Search HTTP 200 and six Search HTTP 503 responses. Those six requests encountered upstream HTTP 402. A separate minimal synthetic request confirmed the provider message `Insufficient Balance`. The balance endpoint still reported availability, so that flag is not used as evidence that model calls work.

The final synthetic reruns were also interrupted by the same 402 condition; failed questions stay in the denominator. The successful earlier rounds do not establish final-candidate 100%, and the last complete replay is explicitly a prior candidate. New code is validated offline, with final real-provider acceptance pending available DeepSeek credit. No production deployment or new official run was performed.

All 200 evidence items returned before the final candidate's six provider failures passed the same byte/namespace/generation checks. That does not make the six failed requests successful. A reporting bug was also corrected: provider failures now explicitly contribute zero to declared source-coverage/recall metrics; they cannot disappear as unknown values. Historical aggregate reports retain both the originally recorded summary and the recomputed metric view.

## Reproducible quality criteria

`experiments/evaluate.py --answer --require-pass` requires answer correctness, no measured disclosure leakage, all declared evidence checks and required abstentions together. Reports include the number of scored and unknown evidence cases. It cannot turn an ungraded answer or unverified privacy into a pass. Private answers, raw memory, provider I/O and runtime credentials stay under ignored `logs/`; only content-free aggregates and independent synthetic controls are public.

The 21-question self-authored fixture is copied as `experiments/datasets/answer_replay_controls.json` with identical parsed JSON values and normalized file line endings; the pre-existing local v3 file is untouched. The additional nine-question fixture is `experiments/datasets/context_reasoning_controls.json`. Use the same model/configuration and retain all failed runs for comparisons. Aggregate results and final offline source hashes are in `experiments/reports/answer-replay-progress.json` and `answer-replay-offline.json`.

After provider credit is restored, complete the final chronological replay, run two strict full control passes, rerun the independent loopback HTTP controls, and inspect answer-level disagreements with exact source support. Preserve all runs, including provider failures. Achieving those local gates would still require a new official run to establish the competition score.
