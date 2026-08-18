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
- contain at least one fixed lexical token before the placeholder and at least
  one fixed lexical token after the placeholder;
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

## D20 — Validators return stable ordered violation codes

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T5, T6

Deterministic validators report invalid vocabulary artifacts as stable
violation codes rather than raising exceptions for ordinary validation failure.

The public validator boundary is:

```python
validate_forge_unit(unit: VocabUnit) -> tuple[str, ...]
validate_context_bank(unit: VocabUnit) -> tuple[str, ...]
```

An empty tuple means the artifact passes that validation stage.

Example:

```python
()
```

means PASS, while:

```python
(
    "F_SOURCE_REF_INVALID",
    "F_SOURCE_SENTENCE_EMPTY",
)
```

means FAIL.

### Stability

Violation codes are part of the internal protocol.

They must be:

- deterministic;
- returned in a fixed contract-defined order;
- independent of dictionary/set iteration order;
- suitable for persistence in FORGE rejection events;
- changed only through an explicit contract change.

Human-readable error wording is not part of the stable protocol.

### Exhaustive validation

A validator should report all independent violations it can determine in one
pass rather than stopping at the first failure.

However, dependent checks are suppressed when their prerequisite has already
failed.

Example:

If `source_sentence` is empty, report:

`F_SOURCE_SENTENCE_EMPTY`

but do not additionally report:

`F_SOURCE_UNIT_MISSING`

because containment cannot be meaningfully evaluated without a source sentence.

Likewise, if `unit_type` itself is invalid, checks whose semantics depend on a
valid unit type must not run.

### Exceptions

Exceptions are reserved for programming errors or misuse of the validator API,
not for an ordinary invalid `VocabUnit`.

**Reason:** T6 must be able to reject an AI-generated artifact deterministically,
show all meaningful reasons to the user, and persist machine-stable rejection
evidence without depending on mutable human-readable messages.

## D21 — Forge validator has a fixed violation inventory

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T5, T6

`validate_forge_unit()` validates only the artifact that must be valid at the
T6 Forge acceptance boundary.

It returns violation codes in the following fixed order:

```python
(
    "F_LEMMA_SLUG_INVALID",
    "F_SENSE_SLUG_INVALID",
    "F_UNIT_KEY_INVALID",
    "F_UNIT_KEY_MISMATCH",
    "F_LEMMA_EMPTY",
    "F_UNIT_TYPE_INVALID",
    "F_UNIT_SHAPE_INVALID",
    "F_TARGET_R_INVALID",
    "F_TARGET_L_INVALID",
    "F_TARGET_W_INVALID",
    "F_TARGET_S_INVALID",
    "F_NO_TARGET_ENABLED",
    "F_STATE_R_INVALID",
    "F_STATE_L_INVALID",
    "F_STATE_W_INVALID",
    "F_STATE_S_INVALID",
    "F_TARGET_STATE_R_MISMATCH",
    "F_TARGET_STATE_L_MISMATCH",
    "F_TARGET_STATE_W_MISMATCH",
    "F_TARGET_STATE_S_MISMATCH",
    "F_REGISTER_INVALID",
    "F_DEFINITION_EMPTY",
    "F_SOURCE_REF_INVALID",
    "F_SOURCE_SENTENCE_EMPTY",
    "F_SOURCE_UNIT_MISSING",
)
```

Only codes whose rule is violated are returned. A code is never returned more
than once.

### Identity rules

`F_LEMMA_SLUG_INVALID`

`lemma_slug` does not match the frozen slug grammar.

`F_SENSE_SLUG_INVALID`

`sense_slug` does not match the frozen slug grammar.

`F_UNIT_KEY_INVALID`

`unit_key` does not match `UNIT_KEY_PATTERN`.

`F_UNIT_KEY_MISMATCH`

`unit_key` is not exactly:

```text
lemma_slug + "::" + sense_slug
```

No normalization or automatic repair is allowed.

### Lexical rules

`F_LEMMA_EMPTY`

`lemma` is empty after trimming whitespace.

`F_UNIT_TYPE_INVALID`

`unit_type` is not one of `UNIT_TYPE_VALUES`.

`F_UNIT_SHAPE_INVALID`

The lexical Unit does not satisfy the frozen D19 shape for its valid
`unit_type`.

Examples include:

- a `word` that normalizes to more than one lexical token;
- a `chunk` with fewer than two lexical tokens;
- an invalid v0 `frame` placeholder structure.

This check runs only when:

- `lemma` is non-empty; and
- `unit_type` is valid.

A shape failure must become this violation code. It must not leak a normal
matcher `ValueError` through the public validator boundary.

### Target rules

Each `Target_*` field must be exactly one of:

```python
("", "1")
```

Invalid fields map to their channel-specific code:

```text
Target_R -> F_TARGET_R_INVALID
Target_L -> F_TARGET_L_INVALID
Target_W -> F_TARGET_W_INVALID
Target_S -> F_TARGET_S_INVALID
```

`F_NO_TARGET_ENABLED`

is returned only when all four target flags are individually valid and none is
`"1"`.

If any target flag is invalid, this aggregate check is suppressed because the
validator cannot safely determine whether the Unit has no intended target.

### State rules

Each `state_*` field must be either:

- `""`; or
- one of the persisted `STATES`.

`UNKNOWN` is diagnostic only and is not a valid persisted state.

Invalid states map to:

```text
state_R -> F_STATE_R_INVALID
state_L -> F_STATE_L_INVALID
state_W -> F_STATE_W_INVALID
state_S -> F_STATE_S_INVALID
```

For each channel, target/state presence must satisfy:

```text
Target_X == "1"  <=>  state_X is non-empty
```

Mismatch codes are:

```text
R -> F_TARGET_STATE_R_MISMATCH
L -> F_TARGET_STATE_L_MISMATCH
W -> F_TARGET_STATE_W_MISMATCH
S -> F_TARGET_STATE_S_MISMATCH
```

A channel's mismatch check runs only when both its target flag and its state
value are individually valid.

T5 does not decide lifecycle transitions or graduation. Those remain owned by
T9 reconciliation.

### Register and definition rules

`F_REGISTER_INVALID`

`register` is not one of the frozen `REGISTER_VALUES`.

`F_DEFINITION_EMPTY`

The English learner definition is empty after trimming whitespace.

T5 deliberately introduces no arbitrary minimum character count as a proxy for
definition quality.

### Evidence rules

`F_SOURCE_REF_INVALID`

`source_ref` does not fully match `SOURCE_REF_PATTERN`.

This is syntax-only validation. The validator must not resolve the reference or
check that the external resource exists.

`F_SOURCE_SENTENCE_EMPTY`

`source_sentence` is empty after trimming whitespace.

`F_SOURCE_UNIT_MISSING`

The source sentence does not contain the lexical Unit according to the shared
D19 `contains_unit()` semantics.

The containment check runs only when all of these prerequisites pass:

- `source_sentence` is non-empty;
- `lemma` is non-empty;
- `unit_type` is valid;
- the Unit shape is valid.

### Explicitly outside this validator

`validate_forge_unit()` does not require or validate:

- `Ctx_1..Ctx_5`;
- audio or other media;
- lifecycle/graduation timing;
- `target_justification`.

`target_justification` is FORGE event provenance under D16 and is validated by
the T6 producer when a productive target is enabled. It is not a `VocabUnit`
field.

**Reason:** The Forge validator must be strict enough to prevent an invalid
lexical Unit from entering Anki while remaining aligned with the staged
T6 -> T8 -> T9 pipeline. A fixed code inventory also gives T6 a deterministic
rejection protocol that can be tested and persisted.
