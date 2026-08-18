# Vocabulary Learning System — Decision Log

This file is normative for architectural decisions that cannot be fully expressed as machine-checkable constants or data models.

Decisions are append-only. Supersede an old decision with a new decision ID; do not silently rewrite history.

## D09 — Event time separates instant from local calendar day

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T2, T12

- `ts` is an ISO-8601 timestamp normalized to UTC with offset `+00:00`.
- `day` is the local calendar date in `Asia/Ho_Chi_Minh`, formatted `YYYY-MM-DD`.
- `since` filtering is inclusive: `event.ts >= since` after inputs are normalized to UTC.
- `ts` is used for ordering/filtering instants; `day` is used for local-day reports.

**Reason:** Arbitrary ISO-8601 offsets do not sort correctly as raw strings across equivalent instants, while UTC-only timestamps are insufficient for local calendar-day reporting.

## D10 — Leech is a rescue signal, not a state transition

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T9

Anki's `leech` tag is note-level rescue evidence. It is not represented as `* -> LEARNING` or any other lifecycle transition. Per-channel degradation is handled by explicit channel state transitions. Reconciliation may use the leech tag to trigger rescue behavior such as diagnostics or VisualCue intervention.

## D11 — Context overlap excludes Unit tokens

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T5, T8

For source/context similarity checks:

- remove normalized target-Unit tokens before calculating source-token overlap;
- require at least `CTX_MIN_TOKENS = 8` in a generated context;
- apply `CTX_MAX_SOURCE_TOKEN_OVERLAP = 0.60` after Unit-token removal.

**Reason:** The context is required to contain the target Unit, so counting the Unit itself as copied source text over-penalizes valid contexts.

## D12 — STATE events are channel-scoped

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T9, T12

Every `STATE` event records the affected channel in `payload.channel`. Aggregate Unit state is derived and is never persisted as the subject of a `STATE` transition event.

## D13 — Derived aggregate state fails closed

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T1, T9

If any enabled channel has an empty or unrecognized lifecycle state, `VocabUnit.derived_state()` returns the diagnostic sentinel `UNKNOWN` rather than silently deriving a valid lifecycle state from the remaining channels.

`UNKNOWN` is not a lifecycle state, is not included in `STATES`, and has no transitions.

## D14 — Transition-gate data lives at the transition's scope

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T9

Data that gates a per-channel transition must live at the per-channel level. Therefore session evidence, encounter failure, and corpus misuse are represented in `ChannelProgress`, not `UnitProgress`.

Unit-level data is reserved for genuinely Unit-level facts such as the note-level leech tag and timing that requires all active channels to be mastered.

## D15 — Stable context in normal FSRS review

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T3, T8, T12

Normal Anki/FSRS review uses one stable context field:

- `Ctx_1` is the fixed context used during normal card review.
- `Ctx_2` through `Ctx_5` are reserved for novel-context/generalization assessment.
- Normal Anki card templates must not randomly rotate among `Ctx_1..Ctx_5`.

**Reason:** FSRS review should primarily measure memory under a stable stimulus. Randomly changing context can change item difficulty and introduce variance that is unrelated to memory strength.

Generalization is measured separately using contexts that were not used in normal review. This also preserves a pool of novel contexts for Reading and productive-language checkpoints.

Surface variability may be introduced in explicit assessment sessions, but it is not mixed into the normal FSRS scheduling loop.
