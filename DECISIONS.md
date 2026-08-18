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

## D22 — Source copying uses directional multiset overlap

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T5, T8

Context validation measures how much of the generated context is copied from the
source sentence.

It does not use Jaccard similarity.

The metric is directional:

```text
source_copy_ratio =
    residual context tokens also available in residual source
    ----------------------------------------------------------
                total residual context tokens
```

The question being measured is:

```text
"What fraction of this context could have been copied from the source?"
```

### Token representation

Both the generated context and `source_sentence` use the shared D19
normalization and lexical tokenization.

No stemming, lemmatization, fuzzy matching, stop-word removal, or semantic
similarity is used.

Duplicate tokens matter.

The overlap therefore uses token multisets rather than token sets.

For each token `t`:

```text
shared_count(t) =
    min(context_count(t), source_count(t))
```

and:

```text
shared_total =
    sum(shared_count(t) for every residual token t)
```

### Excluding the Unit itself

The target Unit must not inflate the copying score merely because both the
source and generated context are required to contain it.

Before computing overlap, exactly one Unit's fixed lexical-token multiset is
removed independently from each side.

For a `word`, the fixed Unit tokens are the normalized word token.

For a `chunk`, they are all normalized chunk target tokens.

For a `frame`, they are the normalized fixed tokens before and after the
`___` slot. Slot content is not part of the fixed Unit and remains residual
context.

Token subtraction is count-based and removes no more copies of a token than
the Unit itself requires.

### Ratio

After Unit-token subtraction:

```text
source_copy_ratio =
    shared_total / number_of_residual_context_tokens
```

Example:

```text
source:
Climate change may pose a serious threat to food security.

context:
Pollution can pose a major threat to public health.
```

After removing the fixed chunk tokens:

```text
pose / a / threat / to
```

the remaining context tokens are compared directionally against the remaining
source tokens.

### Threshold

The existing frozen threshold remains:

```python
CTX_MAX_SOURCE_TOKEN_OVERLAP = 0.60
```

A context violates the source-copy rule only when:

```text
source_copy_ratio > 0.60
```

Exactly `0.60` is allowed.

If no residual context tokens remain after Unit-token subtraction, the overlap
check is not evaluated. Insufficient context is handled by the context-length
contract instead.

The overlap check runs only after the relevant context has passed the
prerequisites required for meaningful Unit containment.

**Reason:** Jaccard is symmetric and can hide copying when a short generated
context is largely contained inside a much longer source sentence. A
directional multiset ratio directly measures the behavior we actually want to
prevent: generated context that derives too much of its lexical content from
the evidence sentence.

## D23 — Context-bank validation has fixed staged rules

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T5, T8

`validate_context_bank()` validates the five generated context fields only after
the lexical Unit has already passed Forge-stage validation.

The public boundary remains:

```python
validate_context_bank(unit: VocabUnit) -> tuple[str, ...]
```

An empty tuple means PASS.

### Fixed violation order

Violations are returned in this fixed order:

```python
(
    "C_CTX_1_EMPTY",
    "C_CTX_2_EMPTY",
    "C_CTX_3_EMPTY",
    "C_CTX_4_EMPTY",
    "C_CTX_5_EMPTY",

    "C_CTX_1_UNIT_MISSING",
    "C_CTX_2_UNIT_MISSING",
    "C_CTX_3_UNIT_MISSING",
    "C_CTX_4_UNIT_MISSING",
    "C_CTX_5_UNIT_MISSING",

    "C_CTX_1_TOO_SHORT",
    "C_CTX_2_TOO_SHORT",
    "C_CTX_3_TOO_SHORT",
    "C_CTX_4_TOO_SHORT",
    "C_CTX_5_TOO_SHORT",

    "C_CONTEXTS_NOT_DISTINCT",

    "C_CTX_1_SOURCE_COPY",
    "C_CTX_2_SOURCE_COPY",
    "C_CTX_3_SOURCE_COPY",
    "C_CTX_4_SOURCE_COPY",
    "C_CTX_5_SOURCE_COPY",
)
```

Each code appears at most once.

### Presence

All five fields are required at the T8 context-bank boundary:

```text
Ctx_1
Ctx_2
Ctx_3
Ctx_4
Ctx_5
```

A context containing only whitespace is empty.

An empty context emits only its corresponding:

```text
C_CTX_X_EMPTY
```

Checks requiring lexical content are suppressed for that context.

### Unit containment

Every non-empty context must contain the Unit according to the shared D19
`contains_unit()` matcher.

A missing Unit emits:

```text
C_CTX_X_UNIT_MISSING
```

The context validator must not introduce alternate word/chunk/frame matching
semantics.

If the Unit itself is structurally invalid, that is a caller/precondition error:
the Unit should have been rejected by `validate_forge_unit()` before reaching
this stage.

### Context length

Generated context must contain enough lexical material beyond the fixed Unit
itself.

Two minimums apply simultaneously.

First, total normalized lexical-token count must satisfy the existing contract:

```python
CTX_MIN_TOKENS = 8
```

Second, after subtracting exactly one fixed Unit-token multiset using the same
count-based subtraction rule as D22, at least:

```text
6 residual lexical tokens
```

must remain.

Therefore the effective minimum is equivalent to:

```text
max(
    CTX_MIN_TOKENS,
    number_of_fixed_unit_tokens + 6
)
```

for a context containing one required occurrence of the Unit.

For a frame, only the fixed tokens around `___` count as fixed Unit tokens.
Slot content remains residual context.

A failure emits:

```text
C_CTX_X_TOO_SHORT
```

Length is checked only for a non-empty context whose Unit containment check
passes.

### Distinctness

The five contexts must provide different retrieval situations.

Distinctness uses the shared normalized lexical-token representation.

Two contexts are considered duplicates when their complete normalized token
tuples are equal.

Differences in:

- capitalization;
- Unicode normalization;
- punctuation only;

do not make two contexts distinct.

If any pair of non-empty contexts is duplicate after normalization, emit exactly:

```text
C_CONTEXTS_NOT_DISTINCT
```

once.

Distinctness is independent of context length. A short context may therefore
produce both:

```text
C_CTX_X_TOO_SHORT
```

and:

```text
C_CONTEXTS_NOT_DISTINCT
```

when both facts are true.

Empty contexts do not participate in duplicate comparison.

### Source-copy protection

Each context that:

- is non-empty; and
- contains the Unit;

is checked against `source_sentence` using the D22 directional multiset
`source_copy_ratio`.

The Unit's fixed lexical-token multiset is removed independently from context
and source before overlap is measured.

The frozen threshold is:

```python
CTX_MAX_SOURCE_TOKEN_OVERLAP = 0.60
```

A context fails only when:

```text
source_copy_ratio > 0.60
```

Exactly `0.60` passes.

A failure emits:

```text
C_CTX_X_SOURCE_COPY
```

If no residual context tokens remain, the copy check is suppressed. The
context-length rule owns that failure.

### Stage precondition

`validate_context_bank()` assumes that the lexical/core Unit has already passed:

```python
validate_forge_unit(unit) == ()
```

T8 must not use context validation as a substitute for Forge validation.

The context validator therefore does not re-report Forge `F_*` violations.

### Explicitly outside this validator

`validate_context_bank()` does not validate:

- target justification;
- audio filenames or Anki media representation;
- lifecycle state transitions;
- graduation timing;
- TTS voice selection;
- semantic quality beyond the deterministic rules frozen above.

**Reason:** T8 needs a deterministic acceptance boundary for AI-generated
contexts without forcing T6 to create context artifacts prematurely. Presence,
Unit containment, sufficient lexical material, contextual diversity, and
source-copy protection are separate checks and therefore remain independently
observable.

## D24 — EventLog enforces structural event contracts before T6

**Date:** 2026-08-18  
**Status:** Accepted  
**Blocks:** T6

`EventLog` is the append-only persistence boundary for historical evidence.

Before T6 begins producing FORGE events, the log boundary must reject events
that violate the frozen structural event contract.

### Required payload fields

For every emitted event type, `EventLog` must require every field listed in:

```python
EVENT_PAYLOAD_REQUIRED_FIELDS
```

The log owns only unconditional structural requirements.

It does not own producer-specific conditional semantics.

For example, FORGE always requires:

```text
source_ref
accepted
```

but `target_justification` remains conditional producer provenance under D16
and is therefore validated by the T6 FORGE producer rather than made an
unconditional EventLog requirement.

Likewise, rejection-specific violation codes belong to the FORGE producer's
semantic contract.

### REVIEW is reserved

`REVIEW` remains part of the event vocabulary for compatibility and future
design, but v0 does not emit synthetic REVIEW events.

Therefore:

```python
RESERVED_EVENT_TYPES = ("REVIEW",)
```

`EventLog.log()` must fail closed if asked to append a reserved event type.

Existing historical decoding remains separate from this emission rule.

### JSON must be standards-compliant

Serialized event records must use:

```python
json.dumps(..., allow_nan=False)
```

Payload values such as:

```text
NaN
Infinity
-Infinity
```

must not be silently persisted as non-standard JSON tokens.

A serialization failure must occur before any invalid record is appended.

### Layer ownership

The generic EventLog boundary owns:

- known event type;
- non-empty `unit_key`;
- payload is a mapping;
- unconditional required payload fields are present;
- existing model-metadata requirements;
- existing STATE channel validation;
- reserved-event rejection;
- standards-compliant JSON serialization.

The producer owns semantic rules specific to the meaning of an event.

Examples:

```text
FORGE:
accepted == false -> which violation codes must be present?

FORGE:
Target_W enabled -> is target_justification["W"] present and non-empty?

STATE:
is this transition actually allowed by lifecycle policy?
```

Those rules must not be silently pulled into generic EventLog validation.

### No automatic tail repair

This hardening does not add automatic truncated-tail recovery.

The existing fail-closed append behavior remains unchanged.

A malformed tail must not be silently converted into valid history by merely
adding a newline.

Any future recovery operation must be explicit and auditable.

### Read API remains unchanged

`EventLog.read()` continues returning its existing list representation.

T6 does not require an iterator refactor.

**Reason:** T6 will begin writing semantically important FORGE decisions.
The append-only log must enforce stable structural boundaries without becoming
a second implementation of every producer's business rules.

## D25 — T3 verifies card-template semantics without mutation

**Date:** 2026-08-18
**Status:** Accepted
**Blocks:** T3, T4

T3 verification must validate the semantics of normal Anki card templates.
It does not require byte-for-byte template equality.

For each normal card template `X` in `R`, `L`, `W`, and `S`, `Target_X` must
gate generation of card `X`.

Normal review templates must not reference `Ctx_2` through `Ctx_5`. Those
fields remain reserved for novel-context assessment under D15.

Executable JavaScript is forbidden in normal review templates because it can
invalidate deterministic card-generation and context-selection guarantees.

The verifier is read-only and fails closed. It must not create, update, repair,
or otherwise mutate the installed Anki note type.

CSS is not a semantic PASS/FAIL contract in v0. Cosmetic CSS changes therefore
do not cause semantic verification failure.

This decision does not add the proposed C8 pedagogical answer-leak table for
the R/L/W/S templates. No new channel-specific answer-content requirements are
introduced here.
