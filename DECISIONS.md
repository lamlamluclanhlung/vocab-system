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

## D16 — Productive-target justification is FORGE provenance

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T6

`Target_W` and `Target_S` remain note-state flags only:

- enabled: `"1"`
- disabled: `""`

They never store explanatory prose.

When FORGE enables a productive channel (`W` or `S`), the reason for that
decision is historical provenance and belongs in the FORGE event payload under:

`target_justification`

The payload is keyed by channel, for example:

```json
{
  "target_justification": {
    "W": "Useful productive collocation for formal writing.",
    "S": "Common spoken phrase worth active retrieval."
  }
}
```

A justification is required only for productive channels actually enabled by
that FORGE decision.

The justification must be a non-empty string after trimming whitespace.
No arbitrary minimum character count is used as a proxy for quality.

Reason: Anki notes store current vocabulary state. The append-only event log
stores why a historical targeting decision was made. Mixing justification prose
into Target\_\* fields would combine state and provenance and break the frozen
"1" | "" target contract.

## D17 — Unit identity is immutable after note creation

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T4, T5, T6

A vocabulary Unit receives its stable identity at creation time.

The following persisted Anki note fields are immutable through the normal
update path:

- `unit_key`
- `lemma_slug`
- `sense_slug`

`unit_key` must remain derived from the approved identity slugs:

`lemma_slug + "::" + sense_slug`

Changing one of these fields would represent a different Unit identity rather
than an ordinary update to learning state or lexical content.

`lemma` is deliberately not included in this immutable set. Display or lexical
content may require correction without changing the approved Unit identity.

The Anki persistence boundary must fail closed if a normal field update attempts
to modify any immutable identity field.

This restriction is a storage invariant, not lifecycle policy. Mutable fields
such as `Target_*`, `state_*`, contexts, media, and lifecycle metadata are
controlled by their owning producers or reconciliation policy.

**Reason:** Anki is the primary vocabulary store. Protecting identity only in
the Python model is insufficient if the persistence write path can mutate the
same identity directly.

## D18 — Validation is staged by artifact maturity

**Date:** 2026-08-18
**Status:** Accepted
**Blocks:** T5, T6, T8

Validation is not one monolithic requirement over a fully hydrated
`VocabUnit`.

A Unit becomes complete in stages.

### Forge-stage validation

T6 validates the lexical/core artifact before context generation.

`validate_forge_unit()` owns invariants such as:

- stable identity;
- valid `unit_type`;
- target/state consistency;
- valid register;
- non-empty learner definition;
- valid `source_ref` syntax;
- source sentence contains the target Unit.

At this stage, `Ctx_1..Ctx_5` and media fields may still be empty.

### Context-stage validation

T8 generates the context bank and then runs:

`validate_context_bank()`

This boundary owns invariants such as:

- all required contexts are present;
- every context contains the target Unit;
- contexts are pairwise distinct;
- sufficient context exists beyond the Unit itself;
- generated context does not copy the source sentence beyond the allowed
  overlap threshold.

Media-specific validation belongs to the stage that defines the persisted media
representation and is not frozen in T5.

Lifecycle/graduation policy belongs to reconciliation in T9 and is not pulled
back into lexical validation.

**Reason:** Requiring a fully hydrated note at the T6 acceptance boundary would
make T6 depend on artifacts that are intentionally created only in T8.
Validation must remain strict without reversing the build pipeline.

## D19 — Unit matching uses one deterministic token matcher

**Date:** 2026-08-18
**Status:** Accepted
**Blocks:** T5, T10

T5 validation and T10 corpus scanning must use the same exported Unit-matching
semantics. They must not maintain separate implementations.

### Token normalization

Before matching:

1. normalize Unicode with NFKC;
2. case-fold text;
3. normalize common Unicode apostrophes to ASCII `'`;
4. tokenize deterministically;
5. punctuation and hyphens act as token boundaries;
6. an apostrophe inside a word remains part of that token.

Examples:

`Don't` -> `don't`

`state-of-the-art` -> `state`, `of`, `the`, `art`

### word

A `word` Unit must normalize to exactly one lexical token.

A text contains the Unit when that token appears as a complete normalized token.

Substring matching is not allowed.

Example:

`art` does not match `partial`.

### chunk

A `chunk` Unit is represented by two or more fixed lexical tokens and contains
no frame placeholder.

A text contains the chunk when there exist ordered token positions for all
target tokens such that:

- target-token order is preserved;
- every target token matches exactly;
- at most 2 non-target tokens are inserted IN TOTAL between the first and last
  target token.

Formally, for matched positions:

`i1 < i2 < ... < in`

the allowed insertion count is:

`sum(i[j+1] - i[j] - 1) <= 2`

Example:

`pose a threat to`

matches:

`pose a serious threat to`

and:

`pose a very serious threat to`

but does not match a form requiring 3 inserted tokens.

### frame

The canonical frame placeholder for v0 is:

`___`

A v0 frame must:

- contain exactly one `___` placeholder;
- contain at least two fixed lexical tokens in total;
- contain no other placeholder syntax.

During matching, the placeholder must consume at least 1 and at most 6 lexical
tokens.

The fixed tokens before and after the placeholder must match exactly and in
order.

Examples:

`it is ___ that`

matches:

`It is widely believed that`

but does not match a form where the slot exceeds the configured v0 slot limit.

Frames with multiple slots are deliberately out of scope for v0. Supporting
them later requires an explicit contract change rather than silently changing
matching semantics.

### Shared implementation

T5 will export the normalization and matching helpers.

T10 `corpus.py` must import and reuse those helpers instead of reimplementing
word/chunk/frame matching.

**Reason:** If FORGE validation and corpus scanning use different matching
rules, the same Unit could be accepted as present in its evidence sentence but
later be reported as absent from the corpus. One deterministic matcher prevents
that semantic drift.
