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

## D26 — T7 leech configuration is human-set and read-only verified

**Date:** 2026-08-18
**Status:** Accepted
**Blocks:** T7, T9

T7 combines human configuration through the Anki GUI with deterministic,
read-only verification.

The canonical engineering threshold is `LEECH_LAPSE_THRESHOLD`.
`ANKI_LEECH_THRESHOLD` derives from that canonical threshold.

The required Anki leech action is Tag Only, not automatic Suspend.

Verification operates on one runtime deck explicitly supplied by the caller.
Deck and option-preset identity are not frozen in v0. A dedicated vocabulary
preset is operationally recommended, but its name, ID, and sharing status are
not verifier PASS/FAIL invariants.

T7 never creates, updates, deletes, assigns, or otherwise mutates Anki deck
configuration.

Under D10, T7 does not interpret a leech as a lifecycle transition and does not
attribute the note-level leech signal to an R/L/W/S channel. T7 emits no event
and changes no note, card, media, tag, or lifecycle state.

Lifecycle response and remediation remain owned by future T9 under D10.

## D27 — T8 context bank is one confirmed atomic logical artifact

**Date:** 2026-08-19
**Status:** Superseded by D30
**Blocks:** T8

`Ctx_1` through `Ctx_5` are generated together by exactly one LLM request and
returned as one strict structured object containing exactly those five string
fields. `validate_context_bank()` validates the whole candidate; any violation
rejects the whole bank. T8 performs no automatic retry, field-by-field
regeneration, repair, or partial persistence.

Initial persistence requires a human preview and explicit confirmation. T8
then rereads the note and compares the generation-relevant source snapshot
before writing. If it changed, the write is rejected as stale. The accepted
bank is written by one explicit subset update containing all five context
fields, followed by exact readback verification.

An existing valid bank is never silently overwritten. A partial bank or a
fully populated invalid bank fails closed. T8 v0 has no normal context
regeneration feature; this does not preclude a separately designed change
workflow in a future version. T8 emits no EventLog event.

## D28 — Normal R/L review uses stable persisted artifacts

**Date:** 2026-08-19
**Status:** Superseded by D31
**Blocks:** T3, T8

The exact Anki card-generation requirements are:

```text
R -> Target_R AND Ctx_1
L -> Target_L AND audio_1
W -> Target_W
S -> Target_S
```

No additional hidden field may become a generation requirement. Normal R
review uses stable `Ctx_1`; normal L review uses stable `audio_1`. Normal
review must not reference `Ctx_2` through `Ctx_5` or `audio_2`/`audio_3`.

All three baseline audio slots speak the exact accepted `Ctx_1` text and vary
only by configured speaker:

```text
audio_1 = Ctx_1 spoken by voice_1
audio_2 = Ctx_1 spoken by voice_2
audio_3 = Ctx_1 spoken by voice_3
```

Thus `Ctx_2` through `Ctx_5` own context variation, while `audio_2` and
`audio_3` own alternate-speaker variation. T8 synthesizes only when
`Target_L == "1"`. Otherwise it performs no synthesis and neither clears nor
mutates existing audio.

## D29 — T8 audio artifacts have immutable request identity

**Date:** 2026-08-19
**Status:** Superseded by D32
**Blocks:** T8

T8-created audio files are immutable request-identified artifacts. Cloud TTS
byte reproducibility is not assumed; the reproducible object is the logical
synthesis request.

Canonical synthesis request v1 contains exactly:

```text
{
  "v": 1,
  "provider": "azure-speech-rest",
  "region": "<exact runtime region>",
  "unit_key": "<exact persisted unit_key>",
  "slot": <1|2|3>,
  "source_context_field": "Ctx_1",
  "text": "<exact persisted Ctx_1>",
  "voice_id": "<slot voice ID>",
  "locale": "<runtime common locale>",
  "output_format": "audio-24khz-48kbitrate-mono-mp3"
}
```

`slot` is serialized as the integer `1`, `2`, or `3`. Canonical bytes are:

```python
json.dumps(
    request,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Identity is `sha256(canonical_bytes).hexdigest()[:16]`. Filenames are exactly
`vocab-a1-<16 lowercase hex>.mp3`, `vocab-a2-<16 lowercase hex>.mp3`, and
`vocab-a3-<16 lowercase hex>.mp3` for slots 1, 2, and 3 respectively.

Identity never uses Python `hash()`, timestamps, UUIDs, raw `unit_key` in the
filename, credentials, or generated audio bytes. Existing complete valid T8
audio remains accepted if runtime region or voice configuration later changes;
new configuration applies only to not-yet-hydrated audio. T8 v0 performs no
automatic audio regeneration or media deletion.

## D30 — Human-mediated ChatGPT context batch

**Date:** 2026-08-19
**Status:** Accepted
**Supersedes:** D27
**Blocks:** T8

Python performs no LLM call.

Context generation flows through:

```text
vocab-system
-> deterministic versioned request JSON
-> human uploads file to ChatGPT Plus
-> ChatGPT returns structured response JSON
-> human imports response JSON
-> deterministic parsing
-> stale identity validation
-> validate_context_bank()
-> per-Unit human confirmation
-> one atomic five-context subset write
-> exact readback
```

`Ctx_1` through `Ctx_5` remain one atomic logical artifact per Unit. The old
invariant that one Unit equals exactly one LLM HTTP request is abolished. One
physical batch may contain many Units.

A batch is neither a database nor a transaction. Persistence atomicity is per
Unit.

There is no retry, automatic repair, partial context regeneration, automatic
regeneration of an accepted context bank, or EventLog event.

## D31 — Single active Listening artifact

**Date:** 2026-08-19
**Status:** Accepted
**Supersedes:** D28
**Blocks:** T3, T8

Normal generation semantics remain exactly:

```text
R -> Target_R AND Ctx_1
L -> Target_L AND audio_1
W -> Target_W
S -> Target_S
```

Normal R uses stable persisted `Ctx_1`. Normal L uses stable persisted
`audio_1`. `Ctx_2` through `Ctx_5` remain forbidden in normal review.

`audio_1` is the only active T8 audio field. `audio_2` and `audio_3` remain in
`NOTE_FIELDS` and `VocabUnit` only for schema compatibility. They are reserved
and opaque.

T8 must never:

- generate `audio_2` or `audio_3`;
- require `audio_2` or `audio_3`;
- parse them to make hydration decisions;
- validate their content;
- clear or overwrite them;
- use them in stale guards.

Existing values must be preserved byte-for-byte and string-for-string. Only
`Target_L == "1"` requires `audio_1`.

The old invariant that `audio_1`, `audio_2`, and `audio_3` must be all empty or
all populated is abolished.

## D32 — Immutable local Kokoro audio_1 artifact

**Date:** 2026-08-19
**Status:** Accepted
**Supersedes:** D29
**Blocks:** T8

New speech synthesis uses Kokoro-82M locally. No Azure concept remains in the
active TTS contract. In particular, `region` is removed rather than replaced
by a fake value such as `"local"`.

New `audio_1` artifacts are request-identified immutable artifacts. Canonical
logical request identity contains exactly:

```text
v
provider
kokoro_package_version
model_id
model_revision
model_sha256
voice_id
voice_sha256
lang_code
speed
inference_device
sample_rate
channels
pcm_format
encoder_id
encoder_version
bit_rate_kbps
encoder_quality
output_format
unit_key
slot
source_context_field
text
```

Canonical bytes remain:

```python
json.dumps(
    request,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Filename identity is `sha256(canonical_bytes).hexdigest()[:16]`. The new
filename is `vocab-a1-<16 lowercase hex>.mp3`.

Generated bytes are not assumed to be reproducible. An accepted existing
`audio_1` is never silently regenerated because local runtime configuration
changes.

Legacy accepted `audio_1` artifacts from the previous provider remain accepted
when the markup is structurally valid for the legacy/new a1 filename form and
the referenced media exists and is non-empty. Current Kokoro configuration is
not inspected before accepting such an artifact.

Malformed or missing-media existing `audio_1` fails closed and is not
automatically repaired. Media is never deleted automatically.

## D33 — Anki observation and card attribution

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9

T9 observes one `VocabularyUnit` note with `notesInfo`. Card IDs come from the
note's `cards` value; normal per-note reconciliation does not require
`findCards`. T9 obtains current card observations with `cardsInfo`.

Before attributing any card, the installed `VocabularyUnit` model must pass the
existing semantic model verifier. Channel attribution is exactly:

```text
card.ord
-> ordinal in the verified current model snapshot
-> template name
-> CHANNEL_BY_TEMPLATE_NAME
-> R/L/W/S
```

An ordinal is only a runtime lookup key. It is never the semantic channel
identity. T9 must not hard-code an ordinal/channel association, even if a
runtime snapshot currently has `0 -> R`, `1 -> L`, `2 -> W`, and `3 -> S`.

Each enabled `Target_X` must resolve to exactly one card for template `X`.
Reconciliation fails closed for the Unit on any of these observations:

- unknown card ID;
- `cardsInfo` cardinality mismatch;
- card belongs to another note;
- unknown ordinal;
- unknown template name;
- duplicate cards for one enabled channel;
- enabled channel has no card;
- card exists for a disabled target channel;
- malformed interval, lapses, queue, or revlog shape.

The frozen Anki revlog meanings are:

```text
0 = learning
1 = review
2 = relearning
3 = cram
```

Only types `0`, `1`, and `2` are lifecycle review evidence. Type `3` is not
lifecycle evidence. `ease == 1` means Again.

A lifecycle lapse is exactly:

```text
review.type == REVLOG_TYPE_REVIEW
AND review.ease == REVLOG_EASE_AGAIN
```

Learning or relearning Again does not increment `lapses_last_30_days`. The
first lifecycle review is the earliest revlog entry whose type is one of the
lifecycle types. Channel age is measured from that review:

```text
floor((now_utc - first_review_utc) / 86400 seconds)
```

It is not note age. `cardsInfo.interval` is the current channel interval used
by the STABLE gate. `cardsInfo.lapses` is total card-level lapse evidence and
may be used to attribute leech rescue diagnostics. Suspension is exactly
`queue == -1`; buried states are not suspension.

## D34 — Channel lifecycle gates and clock semantics

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9

`STATE_TRANSITIONS` remains authoritative. T9 may not invent another lifecycle
edge.

The gates are frozen as follows.

### NEW -> LEARNING

The first lifecycle review exists for that exact channel.

### LEARNING -> STABLE

All of these are true for that exact channel:

- `interval_days >= STABLE_MIN_INTERVAL_DAYS`;
- `age_days >= STABLE_MIN_AGE_DAYS`;
- zero lifecycle lapses during the preceding
  `STABLE_ZERO_LAPSE_WINDOW_DAYS`;
- the card is not suspended.

### STABLE -> LEARNING

A lifecycle lapse occurred after entry into the current STABLE episode for
that exact channel.

### STABLE -> MASTERED

The D35 qualifying mastery evidence is satisfied for that exact channel.

### MASTERED -> RELAPSE

A D35 qualifying failed lifecycle assessment occurred after entry into the
current MASTERED episode for that exact channel.

### MASTERED -> DORMANT

Every enabled channel is currently MASTERED, every active channel has
trustworthy committed MASTERED-entry provenance, and:

```text
now_utc - all_channels_mastered_at
    >= MASTERED_TO_DORMANT_DAYS * 86400 seconds
```

where `all_channels_mastered_at` is the latest MASTERED-entry instant among all
enabled channels.

This is elapsed UTC duration, not the "31st calendar day". It does not use
`Event.day` for timing and does not use the mutable `graduated` note field as
authority. Dormancy is a Unit-level gate that materializes as one per-channel
transition for every active channel.

### DORMANT -> RELAPSE

A qualifying failed D35 lifecycle assessment for that exact channel occurred
after the channel entered DORMANT. Other dormant channels remain DORMANT.

### RELAPSE -> LEARNING

The first lifecycle review for that exact card occurred after RELAPSE entry.

No transition may use evidence from another channel. Missing or ambiguous
transition evidence means no transition and no STATE COMMIT; reconciliation
fails closed and reports insufficient evidence. Aggregate `VocabUnit` state
remains derived only and is never persisted.

## D35 — Lifecycle assessment evidence

**Date:** 2026-08-22
**Status:** Accepted
**Supersedes:** D14's pre-T9-v2 session, encounter-failure, and corpus-misuse
field representation; D14's transition-scope principle remains accepted.
**Blocks:** T9, T11, T12

`JUDGE` is the only assessment event type that directly gates lifecycle
mastery or relapse decisions in T9.

`SPEAK` alone never changes lifecycle state. A future speech assessment may
emit SPEAK evidence, but a producer that intends the result to affect state
must also emit a qualifying channel-scoped JUDGE record.

`ENCOUNTER` alone never changes lifecycle state. ENCOUNTER may make a dormant
Unit a candidate for assessment; only a subsequent failed qualifying JUDGE may
cause DORMANT -> RELAPSE.

The existing generic JUDGE fields remain:

```text
channel
passed
model_id
model_version
```

A JUDGE record is lifecycle-eligible only when it also contains:

```text
assessment_id
stimulus_ref
novel
```

`assessment_id` is a non-empty stable identity for the assessment.
`stimulus_ref` is a non-empty stable identity for the assessment stimulus.
`novel` must be an actual Boolean and must be `True` for mastery evidence.

These additional fields are producer-level lifecycle requirements. They do not
make old `EVENT_SCHEMA_VERSION=1` logs unreadable when a historical or
non-lifecycle JUDGE lacks them.

STABLE -> MASTERED requires `MASTERED_MIN_SESSION_PASSES` qualifying passed
JUDGE records for the same channel. The records must have distinct
`assessment_id` values, distinct `stimulus_ref` values, and be consecutive
qualifying results after entry into the current STABLE episode. Adjacent
qualifying passes must be separated by at least
`MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS` elapsed UTC days. A qualifying failed
JUDGE breaks the consecutive-pass sequence.

A qualifying failed JUDGE after MASTERED or DORMANT entry can gate the
corresponding RELAPSE transition.

T9 checks structure, channel, ordering, distinct identity, timing, and
`novel == True`. The future T11/T12 producer owns truthful construction of
`stimulus_ref` and the assessment semantics.

## D36 — Dormancy retains all artifacts

**Date:** 2026-08-22
**Status:** Accepted
**Supersedes:** The historical dormancy media-stripping rule in
`docs/VOCAB_SYSTEM_SPEC.md`.
**Blocks:** T9

`DORMANT_CLEAR_FIELDS` is exactly the empty tuple.

Dormancy v0 means:

- lifecycle state becomes DORMANT;
- active cards are suspended.

Dormancy does not:

- clear `audio_1`;
- clear `audio_2` or `audio_3`;
- clear `VisualCue`;
- delete physical Anki media;
- delete the note;
- delete or reset revlog;
- regenerate media.

`DORMANT_DELETE_NOTE` remains `False` and `DORMANT_PRESERVE_REVLOG` remains
`True`. Suspension alone removes normal review workload. Retaining artifacts
makes dormancy reversible and avoids unnecessary regeneration or loss of
legacy media identity. D31 and D32 remain authoritative for T8 audio ownership.

## D37 — Safe suspension and selective reactivation

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9

MASTERED -> DORMANT automatically suspends only active-channel cards.
Reconciliation never calls `deleteNotes`.

T9 v0 never automatically unsuspends a card. Anki does not expose trustworthy
card-level provenance proving that a current suspension should be removed by
T9 rather than preserved as a human decision.

DORMANT -> RELAPSE changes only the failed channel's state. Other dormant
channels remain DORMANT and remain suspended. If the failed card is suspended,
T9 reports that selective reactivation is required. Unsuspension requires an
explicit human-confirmed action, and that action may unsuspend only the failed
channel's card. Existing or manual suspension must never be silently removed.

RELAPSE -> LEARNING still requires an actual lifecycle review after RELAPSE
entry. A Unit may therefore legitimately remain with `state_X == RELAPSE` and
`card_X` still suspended until a human explicitly reactivates the card.

`RELAPSE_REACTIVATE_FAILED_CHANNEL_ONLY` remains `True`.
`T9_AUTO_UNSUSPEND` is `False` and
`T9_UNSUSPEND_REQUIRES_HUMAN_CONFIRMATION` is `True`.

Leech remains a note-level rescue signal and never directly causes a lifecycle
transition. `cardsInfo.lapses` may identify channel-specific rescue candidates,
but T9 v0 reports rescue diagnostics only. It does not automatically create a
`VisualCue` or change target flags. Accordingly,
`T9_LEECH_AUTO_TRANSITION` and `T9_LEECH_AUTOCREATE_VISUAL_CUE` are both
`False`.

## D38 — Crash-safe two-phase STATE journal

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9, T12

T9 spans two independent persistence boundaries: EventLog and Anki. There is
no shared transaction. Neither `update Anki -> append event` nor
`append event -> update Anki` is crash safe by itself.

T9 therefore uses an append-only, two-phase STATE journal in the existing
EventLog. It does not add SQLite, PostgreSQL, or another registry.

Each logical transition has a deterministic `transition_id`. T9-produced STATE
records have one of three phases, in order:

```text
PREPARE
COMMIT
ABORT
```

The closed trigger vocabulary is:

```text
FIRST_REVIEW
STABILITY_GATE
REVIEW_LAPSE
MASTERY_ASSESSMENT_PASS
ASSESSMENT_FAIL
DORMANCY_ELAPSED
RELAPSE_REVIEW
```

Every T9-produced STATE payload contains:

```text
channel
from
to
trigger
transition_id
phase
evidence
```

`transition_group_id` is optional and is used for coordinated Unit-level
dormancy. These are producer requirements; the global v1 decoder is unchanged
so historical STATE records remain readable.

### Deterministic transition identity

`transition_id` is the lowercase full 64-hex SHA-256 digest. It never uses a
UUID, randomness, Python `hash()`, or a current timestamp as identity entropy.

The canonical identity input contains:

```text
v
unit_key
channel
from
to
trigger
from_episode_id
evidence
```

Canonical bytes are:

```python
json.dumps(
    identity,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

and the ID is:

```python
sha256(canonical_bytes).hexdigest()
```

The initial NEW episode uses one deterministic sentinel derived from
`unit_key + channel`. Otherwise `from_episode_id` is the `transition_id` of the
latest COMMITTED transition that entered the current state.

Evidence must contain stable identifiers sufficient for rerunning the same
logical decision to produce the same `transition_id`. At minimum:

- FIRST_REVIEW: revlog ID;
- REVIEW_LAPSE: lapse revlog ID;
- MASTERY_ASSESSMENT_PASS: qualifying assessment IDs and the decisive
  assessment ID;
- ASSESSMENT_FAIL: `assessment_id`;
- RELAPSE_REVIEW: revlog ID;
- STABILITY_GATE: first lifecycle review ID, latest lifecycle review ID,
  latest lifecycle lapse ID or null, current `interval_days`, and eligibility
  boundary;
- DORMANCY_ELAPSED: committed MASTERED-entry transition IDs for every active
  channel, `all_channels_mastered_at`, and eligibility boundary.

### Execution protocol

T9 computes a deterministic TransitionPlan. If the `transition_id` has no
existing terminal record, T9 appends and fsyncs PREPARE before Anki mutation.
It then materializes the explicit subset state update, performs required
automatic side effects (currently only dormancy suspension), reads back the
required Anki state exactly, and appends COMMIT only after materialization is
verified.

### Recovery

For PREPARE without COMMIT or ABORT:

- if the old state still exists and the exact frozen evidence still supports
  the same plan, resume materialization and then COMMIT;
- if target state and side effects are already present, verify the exact state
  and then COMMIT;
- if current state or evidence conflicts with the prepared plan, do not guess
  and do not overwrite newer state; append ABORT with the same `transition_id`
  and report a recovery conflict.

Only COMMIT counts as a completed lifecycle transition for reports and future
state-episode provenance. ABORT never changes lifecycle state.

Repeated reconciliation after COMMIT performs zero duplicate STATE
transitions, zero duplicate state writes, and zero duplicate suspension calls.

### Unit-level dormancy

A coordinated dormancy operation uses one `transition_group_id` and one member
`transition_id` per active channel. T9 PREPAREs every member, updates all active
`state_*` fields in one subset update, suspends the required active cards,
performs exact readback, and then COMMITs every member. It does not persist an
aggregate Unit state.

## D39 — Verifiable T9 journal state identity and chain provenance

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9.1, T9.2, T9.3, T12

D39 hardens the read-only T9 journal observation contract. It refines D38
without rewriting its accepted history. Historical pre-D38 STATE records remain
readable through the unchanged generic v1 event decoder and are not assigned
T9 journal semantics.

Every T9-produced STATE payload contains exactly these required fields:

```text
channel
from
to
trigger
transition_id
from_episode_id
phase
evidence
```

`transition_group_id` remains the only optional T9 STATE payload field. The
generic `EVENT_PAYLOAD_REQUIRED_FIELDS["STATE"]` contract remains unchanged.

### Initial NEW episode identity

The initial episode for an active channel is the lowercase full SHA-256 digest
of canonical JSON containing:

```json
{"channel":"<channel>","unit_key":"<unit_key>"}
```

Canonical JSON uses `ensure_ascii=False`, `sort_keys=True`, and
`separators=(",", ":")`. The episode ID is the literal prefix
`initial-new:` followed by that digest. No timestamp, UUID, randomness, or
Python `hash()` participates in the identity.

### Independently verified transition identity

The observer independently recomputes every T9 `transition_id` from:

```text
v
unit_key
channel
from
to
trigger
from_episode_id
evidence
```

using the same canonical JSON settings and full lowercase SHA-256 digest frozen
by D38. Merely supplying a string that has the shape of a digest is
insufficient: an incorrect digest fails closed.

### Phase integrity

Journal order is authoritative. PREPARE must be the first record for a
`transition_id`, followed by exactly one terminal COMMIT or ABORT. A terminal
record without its preceding PREPARE, a terminal before PREPARE, a duplicate
phase, both terminal phases, or a change to any frozen transition identity
field fails closed. A PREPARE without a terminal record is a valid incomplete
operation and does not change lifecycle state.

### Channel-chain provenance

For each active channel, verified reconstruction starts at `NEW` with the
canonical initial-episode sentinel. COMMITTED transitions are applied in
journal order. Each transition must name the reconstructed current state in
`from` and the reconstructed current episode in `from_episode_id`. The chain
then advances to `to`, and its episode becomes that committed
`transition_id`.

After the journal is processed, the persisted channel state must equal the
reconstructed state. A NEW channel therefore reports the initial sentinel as
its `state_episode_id`; any other reconstructed state reports the most recent
COMMITTED transition ID. `all_mastered_at` is derived only from these verified
chains. No aggregate lifecycle state is persisted.

### Dormancy transition-group identity

The frozen group kind is the literal `DORMANCY`. A dormancy group identity is
the lowercase full SHA-256 digest of canonical JSON containing:

```text
kind
unit_key
member_transition_ids
```

where `member_transition_ids` is sorted before canonicalization and the same
JSON settings are used. When `transition_group_id` is present on a T9 record,
it must already be a lowercase full 64-hex digest. Cross-member group
verification is deferred to T9.3.

## D40 — Current-episode temporal evidence

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9.1, T9.2

Transition-gate evidence that depends on the current lifecycle episode must be
available directly at `ChannelProgress` scope. T9.2 remains a pure
`UnitProgress -> TransitionPlan` decision layer and does not reread EventLog.

`ChannelProgress` gains exactly these fields:

```text
state_entered_at
first_lifecycle_review_after_state_entry_id
first_lapse_after_state_entry_id
```

### Current-state entry

For the deterministic initial NEW episode, `state_entered_at` is the empty
string because that sentinel has identity but no historical COMMIT timestamp.

For every non-NEW current state, `state_entered_at` is exactly the normalized
UTC `ts` of the verified COMMIT transition that entered the current state
episode. It is not note creation time, first review time, PREPARE time,
assessment time, or the time of an unrelated STATE record.

### First post-entry lifecycle review

When `state_entered_at` is empty,
`first_lifecycle_review_after_state_entry_id` is `None`. Otherwise it is the
earliest revlog ID whose type is in `REVLOG_LIFECYCLE_TYPES` and whose
epoch-millisecond instant is strictly greater than the current state-entry
instant. A review at exactly the entry instant does not qualify.

### First post-entry lifecycle lapse

When `state_entered_at` is empty, `first_lapse_after_state_entry_id` is `None`.
Otherwise it is the earliest revlog ID whose type is `REVLOG_TYPE_REVIEW`,
whose ease is `REVLOG_EASE_AGAIN`, and whose epoch-millisecond instant is
strictly greater than the current state-entry instant. Learning and relearning
Again records do not qualify as lifecycle lapses.

Both selections use validated revlog entries sorted by revlog ID, never input
list order. Later reviews or lapses do not replace the earliest qualifying
evidence. The complete raw revlog is private observation input and is not
exposed in the public model. These fields are observation facts only and do
not perform lifecycle transitions.

`LifecycleAssessment` remains the complete deterministic ordered assessment
sequence. T9.2 filters it against `state_entered_at` in memory; no additional
post-entry assessment field is introduced. Aggregate lifecycle state remains
derived and is never persisted.

## D41 — Pure one-step reconciliation decision

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9.2, T9.3

T9.2 is the pure function:

```text
decide_transitions(progress, *, now) -> ReconcileDecision
```

It performs no Anki or EventLog reads or writes and never suspends, unsuspends,
or persists journal phases. One invocation may plan independent transitions
for multiple channels, but at most one transition per channel. A channel is
never recursively advanced through more than one lifecycle edge; a new
observation is required after materialization.

### Frozen decision models

`PlannedTransition` contains, in order, `channel`, `from_state`, `to_state`,
`trigger`, `from_episode_id`, `evidence`, `transition_id`, and optional
`transition_group_id`. `ReconcileDecision` contains `unit_key`, ordered
`transitions`, `suspend_card_ids`, `reactivation_required_card_ids`, and
`leech_rescue_channels`. Neither model introduces persisted aggregate state.

### Validation and ordering

The pure layer fails closed on structurally impossible input, including an
invalid Unit key, duplicate or unknown channels, unknown states, non-integer
card IDs, missing episode identity, invalid current-state entry semantics,
cross-channel assessments, non-normalized assessment or state-entry times, or
future temporal evidence. Explicit aware `now` is normalized to UTC.

All output collections follow frozen `CHANNELS` order. Safety and degradation
outrank promotion: STABLE lapse outranks mastery; MASTERED assessment failure
is checked before dormancy; and any qualifying MASTERED failure prevents
Unit-level dormancy during that pass.

### Assessment gates

Only assessments with `novel is True` participate in lifecycle gates. They
must occur strictly after the current `state_entered_at`; equality does not
qualify. The earliest qualifying failed assessment gates MASTERED or DORMANT
to RELAPSE and contributes exactly `assessment_id` evidence.

For STABLE to MASTERED, novel failures reset the current streak and non-novel
records are ignored. Duplicate assessment identity within a failure-free
streak fails closed. One stimulus identity counts at most once. A pass that is
too early does not break the streak but is not selected. The earliest pass
sequence with distinct assessment and stimulus identities, separated from the
previously selected pass by at least the frozen delay, is decisive. Evidence
contains the selected assessment IDs, corresponding stimulus references, and
the final selected assessment ID.

### Channel transition evidence

- NEW to LEARNING uses the globally first lifecycle review ID.
- LEARNING to STABLE requires every D34 scheduling gate plus first/latest
  lifecycle review identity. Its stable eligibility boundary is the later of
  the first-review age boundary and, when present, the latest-lapse clear
  boundary.
- STABLE to LEARNING uses the earliest post-entry lifecycle lapse ID.
- RELAPSE to LEARNING uses the earliest post-entry lifecycle review ID.

Boundaries are normalized UTC instants derived from frozen evidence, never a
current-time identity value. Missing evidence produces no plan rather than an
invented value.

### Coordinated dormancy

Dormancy is considered only when every active channel is MASTERED, no channel
has a qualifying post-entry failure, verified `all_active_channels_mastered_at`
is available, and the frozen elapsed duration has reached its boundary. Every
member receives identical evidence containing the channel-keyed current
MASTERED episode IDs, the all-mastered instant, and the eligibility boundary.

Member transition IDs are computed first. The shared group ID is the full
lowercase SHA-256 digest of canonical JSON containing the frozen `DORMANCY`
kind, Unit key, and sorted member transition IDs. Member and suspension order
remains `CHANNELS` order. Dormancy is the only decision that populates
`suspend_card_ids`.

A DORMANT channel with qualifying failure plans RELAPSE. When its card is
currently suspended, its ID is reported in
`reactivation_required_card_ids`; T9.2 never unsuspends it.

### Transition identity and diagnostics

Every member transition ID uses the exact D38/D39 canonical identity:
schema version, Unit key, channel, source state, target state, trigger, current
episode ID, and frozen evidence. Canonical JSON uses `ensure_ascii=False`,
sorted keys, compact separators, UTF-8, and full lowercase SHA-256. Randomness,
wall-clock identity entropy, Python `hash()`, note ID, and card ID are absent.

Leech remains diagnostic only. With the leech tag present, channels at or
above `ANKI_LEECH_THRESHOLD` are reported in channel order; this causes no
transition, suspension, target change, or VisualCue mutation. A no-op decision
has empty transitions and suspension IDs while diagnostics may remain present.

## D42 — Recovery-first crash-safe materialization

**Date:** 2026-08-22
**Status:** Accepted
**Blocks:** T9.3

The public automatic persistence API is:

```text
reconcile_unit(note_id, *, anki, event_log, now) -> ReconcileRunResult
```

Every run performs recovery preflight before normal observation. It resolves
one unambiguous incomplete T9 operation first. If recovery appends a COMMIT or
ABORT, or otherwise completes materialization, the run returns or raises its
recovery result immediately and does not plan another lifecycle edge. A later
invocation obtains a fresh observation.

### Run result

`ReconcileRunResult` contains the Unit key, every transition ID COMMITTED in
the current run, the subset recovered from PREPARE records that predated the
run, transition IDs ABORTED while resolving recovery conflicts, required
manual-reactivation card IDs, and leech rescue channels. A no-op has empty
transition-ID tuples. Pure-decision diagnostics remain visible.

### Journal validation and uncertainty

One helper converts a frozen `PlannedTransition` and phase into the exact T9
STATE payload. The optional group ID is emitted only when non-empty. Before
the first PREPARE, materialization independently revalidates the lifecycle
edge, trigger, transition digest, optional group digest, group membership,
and canonical JSON evidence. This detects mutation of the otherwise mutable
evidence dictionary after T9.2.

EventLog remains the only journal and its existing flush/fsync append boundary
is authoritative. PREPARE is durable before any Anki mutation. Transport,
Anki-update, readback, or COMMIT-append uncertainty after PREPARE never causes
an automatic ABORT; the PREPARE remains for deterministic recovery.

### Normal independent transitions

Independent plans execute in `CHANNELS` order. Each appends PREPARE, updates
only its `state_X` field, verifies exact note readback, and then appends COMMIT.
Failure of a later channel does not roll back an earlier COMMIT.

### Normal coordinated dormancy

A dormancy decision is one group. Every member is MASTERED to DORMANT with the
same verified group ID and exact ordered suspension set. All PREPARE records
are appended before one subset update containing only active `state_X` fields.
Every state is read back before suspension. All required cards are then
suspended and read back with `queue == ANKI_QUEUE_SUSPENDED` before any member
COMMIT is appended. No media, target, identity, or revlog field is changed.

### Pending discovery

Recovery uses the D39 parser and identity checks. Historical pre-D38 STATE
records remain non-journal history. A pending transaction has PREPARE and no
terminal. The only supported incomplete shape is one ungrouped transaction or
one dormancy group, which may have a partial/all PREPARE set and may include
already COMMITTED members. Multiple unrelated ungrouped operations, multiple
groups, or grouped/ungrouped mixtures fail closed.

### Source-state recovery

When the exact source state remains persisted, recovery performs normal
trustworthy observation and pure decision again. The fresh plan must reproduce
the exact pending transition ID, or for dormancy the exact group ID and member
identities. If it matches, existing PREPARE records are not duplicated;
missing dormancy member PREPAREs are appended before coordinated
materialization, readback, and pending-member COMMITs.

If fresh evidence no longer reproduces the prepared identity, recovery appends
ABORT for currently pending prepared members only, performs no Anki mutation,
and reports a recovery conflict. A failed ABORT append is propagated rather
than hidden.

### Target-state recovery

When the exact ungrouped target already exists, recovery verifies it and
appends COMMIT without repeating the state update. DORMANT to RELAPSE never
unsuspends automatically.

When every dormancy target state already exists, recovery verifies every state
and current queue, suspends only cards not already suspended, verifies all
queues, preserves existing member COMMITs, and appends only missing COMMITs.
This covers crashes after the state update, after suspension, or partway
through member COMMIT appends.

Persisted state that is neither the exact source nor exact target, including a
mixed dormancy source/target pattern, is a recovery conflict. Recovery does not
guess, roll back, or overwrite newer state. It ABORTs still-pending prepared
members where safe and leaves already COMMITTED members untouched.

### Exact readback and reactivation

State readback requires exactly the requested VocabularyUnit note and exact
expected state-field values. Card readback requires exact requested card IDs,
note ownership, actual integer queues, and the required suspension state.

Automatic reconciliation never calls `unsuspend`. Human-confirmed selective
reactivation is the separate API:

```text
reactivate_relapse_channel(
    note_id,
    channel,
    *,
    anki,
    event_log,
    now,
    confirmed,
) -> bool
```

Only literal `confirmed is True` may proceed. Trustworthy observation must show
the requested active channel in RELAPSE. An already-active card returns
`False`. Otherwise exactly that card is unsuspended and exact card readback
must prove it is no longer suspended before returning `True`. This emits no
STATE event; RELAPSE to LEARNING still requires a later lifecycle review.

## D43 — Idempotent partial-ABORT group recovery

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T9.3 closure

A verified dormancy group is logically terminally aborted once any member has
a valid ABORT terminal and no member has a COMMIT terminal. If an ABORT append
fails after earlier members were terminally aborted, the next recovery run
preserves those terminals and appends ABORT only for remaining pending
prepared members, in `CHANNELS` order. It performs no Anki mutation, never
duplicates an ABORT, never invents a missing PREPARE, and reports a recovery
conflict after all prepared members are terminally aborted.

An additional ABORT append failure is propagated. Already durable ABORTs stay
valid and later recovery retries only members whose PREPARE remains pending.
A partially aborted group is never allowed to resume state materialization,
suspension, or COMMIT.

A group containing both COMMIT and ABORT terminals is a permanent recovery
conflict requiring manual intervention. Automatic recovery performs no Anki
mutation and appends no further terminal for that mixed group; it never tries
to overwrite persisted state to make the terminals agree.

When a COMMIT member already conflicts with source or mixed persisted states,
automatic recovery likewise leaves pending members untouched and requires
manual intervention instead of manufacturing a new COMMIT/ABORT mixture.

After a group is completely aborted, its terminal history remains valid D39
journal history and does not itself block unrelated reconciliation. Before a
new PREPARE, however, T9.3 checks the planned transition IDs against existing
journal transactions. Reuse of an aborted or otherwise existing transition ID
fails closed, preventing PREPARE to ABORT to PREPARE and PREPARE to ABORT to
COMMIT histories for the same transition identity.

## D44 — T10 registry snapshot

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

Anki VocabularyUnit notes are the only vocabulary registry. T10 does not add
SQLite, PostgreSQL, a JSON registry, or any duplicate source of truth. One scan
reads the Anki registry exactly once before counting and builds an immutable
in-memory snapshot. Each entry contains exactly `unit_key`, `lemma`, and
`unit_type`, ordered by `unit_key` using deterministic lexical string ordering.

Every valid VocabularyUnit is scanned regardless of lifecycle state or enabled
channel set. T10 is Unit-scoped, never channel-scoped. Before any corpus count
or EventLog append, every entry must have a valid unique Unit key, non-empty
valid lemma, valid existing `UNIT_TYPE_VALUES` member, and a lexical shape that
satisfies the exported D19 matcher contract. T10 reuses `normalize_tokens()`
and `contains_unit()`; it does not create a second Unit-shape implementation.
Any malformed entry or duplicate Unit key fails the entire scan before any
ENCOUNTER emission.

Anki may change after snapshot capture. T10 neither locks nor rereads Anki
during that scan and describes the frozen lexical values observed at snapshot
time. T10 never writes Anki.

`lemma` remains mutable under existing project rules. Each future T10 event
therefore persists the exact `lemma` and `unit_type` used for that observation.
A global registry-snapshot digest is neither event identity nor a mandatory
cross-Unit equality gate: adding an unrelated Unit later must not invalidate
historical observations or prevent that new Unit receiving a missing event for
an unchanged old corpus. If an existing stable Unit key later has changed
lexical provenance and its existing encounter slot is revisited, its unchanged
encounter ID and changed payload conflict fail closed.

Registry locking, another registry database, automatic lexical repair,
channel-specific counts, and sense disambiguation are out of scope.

## D45 — T10 corpus snapshot

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

The v0 corpus artifact is exactly:

```text
<corpus_root>/<YYYY-MM>/*.txt
```

The caller explicitly supplies `corpus_root`, logical `source`, and `month`.
Month must match `YYYY-MM` with a numeric month from `01` through `12`. Source
is a stable logical source slug, not an absolute or machine-specific path and
not a transient run identifier.

T10 v0 accepts plaintext `.txt` only, with case-insensitive extension
comparison. Markdown is deferred because raw Markdown can expose code names,
URLs, link destinations, and front matter as false lexical evidence. No partial
Markdown parser is included.

The month directory must exist; a missing directory fails closed while an
existing empty directory is a valid zero-occurrence corpus. Its input is flat:
every direct child must be a regular supported text file. Symlinks, nested
directories, and unsupported direct-child regular files are rejected rather
than ignored. Files are ordered by canonical relative path, and absolute
machine paths never enter identity.

Each file is read exactly once into bytes for a scan. Its SHA-256 identity uses
those exact raw bytes without normalization, so LF/CRLF and BOM/no-BOM are
distinct artifacts. The same captured bytes are decoded as UTF-8; a UTF-8 BOM
is accepted and removed from lexical text. Invalid UTF-8 fails the entire scan.
No event may be emitted until every file has been discovered, read, hashed,
decoded, and validated.

The v0 corpus is prose. A case-insensitive URL-like span beginning `http://`,
`https://`, or `www.` makes the corpus invalid. T10 does not parse or strip
URLs. Corpus identity uses only frozen artifact evidence, never current time.
Corpus data is local-only by default and the repository ignores exactly the
`corpus/` directory.

Markdown, HTML, PDF, DOCX, recursive input, URL extraction, non-UTF-8
encodings, caching/incremental reads, and filesystem locking are out of scope.

## D46 — T10 occurrence counting

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

D46 extends D19 with counting and document boundaries; it does not alter D19
matching semantics.

### Text blocks

Text is split into independent blocks before D19 token matching. A boundary is
one or more sentence terminators (`.`, `!`, `?`, `…`), a blank-line paragraph
boundary, or a file boundary. One ordinary newline within a paragraph is only
whitespace. Commas, colons, semicolons, parentheses, and hyphens do not by
themselves end a block. Chunk and frame matches cannot cross a block boundary.
The deliberately simple rule may under-count around abbreviations and decimals
such as `Dr.`, `e.g.`, or `3.14`; smart segmentation is out of scope.

### Canonical non-overlapping count

Each block is normalized and tokenized with D19. Counting is deterministic,
leftmost, non-overlapping, and canonical. Starting at the leftmost unconsumed
token `s`, compute the canonical D19 alignment beginning exactly at `s`. If it
does not match, advance by one token. If it ends at `e`, count once and resume
at `e + 1`. A later start overlapping an already consumed occurrence is not
counted; a later non-overlapping occurrence is counted.

For `word`, the target must be exactly one normalized lexical token and a match
at `s` consumes only `s`.

For `chunk`, the target has at least two tokens, preserves order, and allows at
most `CHUNK_MAX_INSERTED_TOKENS` non-target tokens in total. At a fixed start,
the first token must match and later target tokens use their earliest available
positions exactly as the current D19 greedy `_contains_chunk` behavior. The
first successful greedy alignment is canonical; alternate subsequences are not
enumerated.

For `frame`, the existing one-slot D19 shape and bounds remain authoritative.
Fixed before-tokens match exactly, then slot sizes are tried from
`FRAME_SLOT_MIN_TOKENS` through `FRAME_SLOT_MAX_TOKENS`; the first slot length
whose fixed after-tokens match is canonical. Multiple satisfying slot lengths
at one start do not create multiple occurrences.

For every valid single block, positive T10 count is equivalent to
`contains_unit(block, lemma, unit_type)` under D19. This equivalence is
mandatory test coverage in the implementation stage.

Frozen examples include:

- `art` in `art art partial art` counts 3;
- `art` in `state-of-the-art` counts 1;
- `pose a threat to` counts once with zero, one, or two total inserted tokens,
  but zero times with three;
- `pose a pose a threat to` counts 1;
- `pose a threat to and pose a threat to` counts 2;
- `He did pose. A threat to public health emerged.` counts 0 for that chunk;
- `it is ___ that` counts once for slot lengths 1 through 6 and zero for 7;
- `it is that it is really that` counts 1 for that frame.

Stemming, extra lemmatization, fuzzy matching, multiple-slot frames, smart
segmentation, semantic disambiguation, and semantics-changing optimization are
out of scope.

## D47 — T10 ENCOUNTER semantics

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

A T10 ENCOUNTER means only deterministic surface-form exposure for one Unit in
one frozen `(source, month)` corpus snapshot. It does not mean the correct
sense, understanding, correct use, learner production, mastery, failure, or
relapse. T10 emits neither STATE nor JUDGE, never directly changes lifecycle
state, and ENCOUNTER alone remains non-lifecycle evidence.

`count` is the total D46 non-overlapping occurrence count across every file and
block. It is an actual integer greater than or equal to zero; Boolean is not an
integer count. It is not a file, line, or paragraph count. T10 emits zero counts
to distinguish scanned-and-absent from never-scanned.

`month` denotes the corpus period independently of Event `ts` and `day`, so a
historical month may be scanned later. `source` names the logical evidence
source. Different evidence classes, such as `reading` and `own-writing`, use
different source IDs when that distinction matters.

Counts are lexical, not sense-specific. Stable Unit keys for different senses
but equivalent matcher inputs may receive identical counts; downstream users
must not interpret or sum those as independent sense evidence.

The generic v1 ENCOUNTER minimum remains exactly:

```text
count, source, month
```

T10 producer payloads require exactly, in order:

```text
count
source
month
producer
scan_version
encounter_id
lemma
unit_type
corpus_snapshot_digest
corpus_file_count
```

The producer constants are `T10_ENCOUNTER_PRODUCER_ID = "t10-corpus"` and
`CORPUS_SCAN_VERSION = 1`. Producer validation requires that exact producer;
an actual integer scan version equal to the frozen version; full lowercase
64-hex encounter and corpus digests; non-empty exact snapshot lemma; an
existing `UNIT_TYPE_VALUES` member; and an actual non-negative integer file
count. Historical generic ENCOUNTER events without producer `t10-corpus`
remain generic readable v1 events and are not retroactively invalidated.

Lifecycle judgments, sense attribution, correctness assessment, and file-level
ENCOUNTER records are out of scope.

## D48 — T10 encounter identity and idempotency

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

T10 has one deterministic event identity. There is no scan ID and no registry
snapshot digest identity. Canonical JSON is UTF-8 encoding of:

```python
json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
)
```

SHA-256 values are full lowercase hexadecimal digests.

`encounter_id` is exactly SHA-256 of canonical JSON containing:

```text
producer: T10_ENCOUNTER_PRODUCER_ID
scan_version: CORPUS_SCAN_VERSION
source
month
unit_key
```

It identifies one producer/version/source/month/stable-Unit slot. Count,
lemma, unit type, corpus digest, wall-clock time, Event `ts`, and Event `day`
are observation provenance and are excluded from identity.

Each file identity is SHA-256 of its exact captured raw bytes. The corpus
snapshot digest is exactly SHA-256 of canonical JSON containing
`scan_version` and a `files` list of `{path, sha256}` objects in canonical
relative-path order. Absolute root, current time, source, month, and registry
contents are excluded.

Before appending, T10 reads ENCOUNTER history. Only events whose producer is
`t10-corpus` belong to its namespace. For each planned encounter:

1. a missing encounter ID is eligible for append;
2. one existing event with an exactly equal complete T10 semantic payload is
   already durable and skipped;
3. the same ID with any differing semantic field is a conflict and the run
   appends nothing;
4. duplicate historical T10 IDs fail closed even when their payloads match.

Before the first new append, every existing event for the same producer,
version, source, and month must have the current corpus snapshot digest. Any
difference fails before append. Corpus content for a started source/month is
therefore immutable.

Registry evolution is distinct: a newly added Unit key may receive its missing
event for an unchanged historical corpus. Changed lemma or unit type for an
existing Unit key retains the encounter ID but conflicts through payload
provenance. Removing a Unit never deletes or rewrites historical ENCOUNTERs.

Changed corpus bytes do not cause an automatic rescan, source revision,
supersession, overwrite, or deletion. T10 fails closed. Automatic revisions,
supersession, EventLog mutation, PREPARE/COMMIT journaling, and a registry
database are out of scope.

## D49 — T10 execution and failure boundary

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

The later T10 implementation has four conceptual phases:

```text
SNAPSHOT
    I/O: read Anki once, read each corpus file once, validate all inputs,
    and compute corpus artifact identity

COUNT
    PURE: no I/O, EventLog, Anki, clock, or randomness

EMIT PREFLIGHT
    read EventLog, verify T10 history and source/month corpus immutability,
    and classify every plan as missing, already durable, or conflict

EMIT
    append only missing ENCOUNTER events in unit_key order
```

No append occurs until the complete registry snapshot, corpus snapshot, all
counts, and complete emit preflight succeed. COUNT must support dry-run/preview
without emission. A malformed registry, corpus, T10 history, or producer
payload fails closed through a typed T10 error in the implementation stage.
Anki, filesystem, EventLog-read, and EventLog-append infrastructure failures
raise.

If appending fails after some ENCOUNTERs were fsynced, T10 does not roll back,
delete, or auto-retry. A later run recomputes the same encounter IDs, skips
exact durable matches, and appends only missing events. No T9-style
PREPARE/COMMIT is needed because T10 writes only one append-only system and
EventLog already fsyncs each deterministic event.

Event order is ascending `unit_key`; file order is ascending canonical relative
path. T10 never writes Anki, suspends or unsuspends cards, changes `state_*`,
emits STATE or JUDGE, deletes or edits EventLog records, calls an LLM or cloud
API, or creates another registry/database. Semantic equivalence outranks
optimization; simple O(number of Units × corpus size) behavior is acceptable.

Concurrency, scheduling, automatic retry, corpus caching/indexing, incremental
scans, and supersession are out of scope.

## D50 — T10 corpus contract hardening

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T10

For the flat v0 corpus, a canonical relative path is exactly the direct-child
filename string returned by the Python filesystem directory entry. It is not
an absolute path and does not include the corpus root, `.` or `..`, or any path
separator. T10 performs no Unicode, separator, dot-segment, or case
normalization. It preserves the filename's exact case. Canonical file order is
ordinary Python lexical order over those exact filename strings, and that exact
string enters each corpus-digest file object's `path`. Duplicate exact
canonical filenames fail closed. Cross-platform path portability is out of
scope.

URL rejection happens over the full decoded plaintext before tokenization,
splitting, or any unit matching. T10 computes exactly
`casefolded_text = decoded_text.casefold()` and rejects a corpus file when any
literal member of `CORPUS_REJECT_URL_PREFIXES` is a substring of
`casefolded_text`. It does not use a URL parser, regex or token boundary,
whitespace stripping, or a clickability heuristic.

The T10 producer payload keyset is closed. The allowed fields constant is an
alias of the required fields tuple:

```python
T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS
)
```

A `t10-corpus` ENCOUNTER payload has exactly that keyset: every field is
required and no additional field is allowed. This producer-specific closure
does not change the generic non-T10 v1 ENCOUNTER contract, whose required
payload tuple remains exactly `("count", "source", "month")`, or
`EVENT_SCHEMA_VERSION`, which remains 1.

During emit preflight, T10 fully validates every historical event in the
`t10-corpus` producer namespace. It recomputes every historical
`encounter_id` from the envelope `event.unit_key` and the payload's exact
`producer`, `scan_version`, `source`, and `month` through the D48 canonical-JSON
and SHA-256 formula. Any mismatch fails closed before any append. T10 performs
the same recomputation for every planned event instead of trusting a supplied
identifier. Generic ENCOUNTER events outside the T10 producer namespace are
unaffected.

`corpus_file_count` is exactly `len(frozen_corpus_snapshot.files)`, including
zero for an empty snapshot. Events derived from the same frozen snapshot carry
the same corpus snapshot digest and the same file count. The value must be an
actual integer greater than or equal to zero. Within historical T10 events for
one producer, scan version, source, and month, equal corpus snapshot digests
with differing file counts are a conflict and fail before append.

The D19 matcher remains the single semantic authority for unit matching. A
future implementation may refactor its shared pure matching primitive to
expose the canonical match end needed by both `contains_unit` and T10's
non-overlapping count. T10 must consume that shared primitive rather than copy
the matching logic into `vocab/corpus.py`. Such a refactor must preserve the
existing public behavior, pass the full validator suite, and leave all frozen
D19 constants unchanged. This design-hardening task does not modify
`vocab/validators.py`.

Corpus scanning implementation, runtime corpus tests, recursive directories,
symlinks, portability normalization, URL parsing, matcher refactoring,
validator changes, EventLog schema changes, lifecycle behavior, scheduling,
retries, caching, incremental scans, and supersession remain out of scope.

## D51 — T11 semantic-assessment boundary and T12 ownership

**Date:** 2026-08-23
**Status:** Accepted
**Blocks:** T11, T12

T11 separates semantic assessment from session provenance and persistence.

T11 has exactly two conceptual layers.

### T11 Core

T11 Core owns deterministic and locally testable assessment mechanics:

- channel-specific request validation;
- deterministic evidence-sufficiency checks;
- D19 target-presence checks only where D52 allows them;
- construction of deterministic semantic-assessment request artifacts;
- strict parsing of semantic-assessor output;
- validation of closed outcome, failure, reason, and provenance codes;
- validation that a human approval decision approves or rejects the exact
  semantic proposal without editing it.

T11 Core performs no:

- Anki read or write;
- EventLog read or write;
- exposure-ledger read or write;
- lifecycle observation or transition;
- T9 call;
- state-field mutation;
- wall-clock-dependent lifecycle decision.

### T11 Semantic Bridge

The semantic bridge is external and may be nondeterministic.

T11 v1 uses a human-mediated ChatGPT Plus workflow:

```text
deterministic request artifact
    ->
human submits through ChatGPT Plus
    ->
structured response artifact
    ->
strict deterministic import
    ->
human APPROVE or REJECT of the exact proposed verdict
```

Python performs no paid LLM API call.

A reviewer may:

```
APPROVE
REJECT
```

A reviewer may not rewrite:

-  PASS to FAIL;
-  FAIL to PASS;
-  failure codes;
-  reason codes;
-  semantic rationale;
-  any other semantic field.

`REJECT` produces an audit-only ABSTAIN result. It is not an alternative

semantic verdict.

Direct human semantic judging and fully automatic local-LLM lifecycle judging

are outside T11 v1.

### T12 ownership

T12 owns:

-  session manifests;
-  runtime attempt identity;
-  cognitive stimulus identity;
-  exact rendered stimulus artifact identity;
-  stimulus exposure reservation;
-  learner-response capture;
-  response artifact identity;
-  novelty proof;
-  lifecycle-field construction;
-  producer-history preflight;
-  JUDGE/SPEAK EventLog emission.

T12 may produce assessment evidence but does not:

-  decide lifecycle transitions;
-  mutate `state_*`;
-  call T9 as part of event production.

T9 remains an independent consumer of EventLog history.

A shared contract module may define identity grammar, canonicalization, and

pure formulas. Runtime construction and persistence ownership remain T12's.

**Reason:** Semantic judgment and historical provenance answer different

questions. Novelty and idempotency depend on prior durable history and must not

be hidden inside a semantic judge. Keeping T11 free of assessment-history I/O

prevents a semantic assessor from becoming a second lifecycle engine.

**Out of scope:** session scheduling, report UI, automatic lifecycle

reconciliation, paid AI APIs, and direct Anki state mutation.

## D52 — Channel-specific assessment evidence

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11, T12

Assessment evidence is channel-scoped. Evidence for one channel never promotes

or degrades another channel.

The v1 assessment task kinds are:

```
R -> reading_comprehension
L -> listening_comprehension
W -> written_production
S -> spoken_production
```

A different task kind requires an explicit future contract rather than being

silently interpreted as one of these four.

### Reading — R

R measures contextual lexical comprehension from written evidence.

The learner response may paraphrase the Unit without repeating the Unit.

Example:

```
stimulus:
    Her meticulous records made the audit easy.

response:
    It means very careful and precise.
```

The response may PASS even though it does not contain `meticulous`.

D19 target-presence matching is therefore not a response gate for R.

A genuine R FAIL requires trustworthy evidence that the learner attributes an

incorrect meaning to the target Unit in the assessed context.

### Listening — L

L measures contextual lexical comprehension from a spoken stimulus.

The learner must not receive the written stimulus text as part of the ordinary

v1 listening-comprehension task.

The response need not repeat or transcribe the Unit.

D19 target-presence matching is therefore not a response gate for L.

A genuine L FAIL requires trustworthy evidence that the learner assigns an

incorrect interpretation to the target Unit in the heard context.

Transcription, spelling, accent discrimination, and speaker discrimination are

different task kinds and do not silently substitute for v1 listening

comprehension.

### Writing — W

W measures productive written use of the Unit.

The task explicitly requires use of the target Unit.

T11 reuses the frozen D19 matcher to test whether the Unit is present in the

captured written response.

```
target absent
    -> OMITTED

target present
    -> semantic assessment
```

Presence alone never means correct use.

A genuine W FAIL requires that the Unit is present and its actual use violates

one of the closed D53 lexical-failure contracts.

### Speaking — S

S v1 measures productive lexical use in speech.

It does not claim to measure:

-  pronunciation quality;
-  accent;
-  prosody;
-  phonetic accuracy;
-  acoustic intelligibility as a separate construct.

The learner produces an immutable raw audio artifact. Only a D56 human-verified

SUCCESS transcript may enter the D19 presence gate.

```
verified transcript + target absent
    -> OMITTED

verified transcript + target present
    -> semantic assessment

unverified / uncertain transcript
    -> ABSTAIN
```

Presence alone never means correct use.

A genuine S FAIL requires that a trustworthy transcript contains the Unit and

its actual lexical use violates one of the closed D53 failure contracts.

### Shared rule

D19 remains the single lexical matching authority wherever presence matching

is required. T11 must not create a second word/chunk/frame matcher.

D19 determines lexical presence only. It does not determine:

-  meaning;
-  sense correctness;
-  grammatical appropriateness;
-  collocational appropriateness;
-  comprehension;
-  mastery.

**Reason:** Recognition, listening comprehension, written production, and

spoken production are distinct constructs. A universal target-presence gate

would incorrectly reject valid R/L paraphrases and contaminate channel

evidence.

**Out of scope:** cross-channel scoring, pronunciation assessment, generic

language proficiency scoring, and band scores.

## D53 — Assessment outcomes and lifecycle-failure semantics

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11, T12

The T11/T12 assessment outcome set is exactly:

```
PASS
FAIL
OMITTED
ABSTAIN
```

For every producer JUDGE or SPEAK result:

```
passed == (outcome == "PASS")
```

`passed == False` alone never means learner failure.

Consumers and reports must distinguish the closed `outcome` value.

### PASS

PASS means sufficient trustworthy evidence demonstrates success for the exact

channel contract in D52.

PASS may become lifecycle evidence only through the complete D35 field set.

### FAIL

FAIL means sufficient trustworthy evidence establishes a genuine lexical error

inside the construct being assessed.

The only v1 lifecycle FAIL codes are:

```
R:
    wrong_meaning

L:
    wrong_interpretation

W:
    semantic_misuse
    collocation_misuse
    form_misuse

S:
    semantic_misuse
    collocation_misuse
    form_misuse
```

R/L FAIL requires explicit evidence of an incorrect lexical meaning or

interpretation.

W/S FAIL requires all of:

-  trustworthy target presence;
-  sufficient evidence of the actual target use;
-  one or more applicable closed lexical-error codes above.

Task noncompliance alone is not lexical failure.

These do not become lifecycle FAIL merely because they are clear or

interpretable:

-  off-topic response;
-  refusal;
-  unrelated response;
-  failure to follow directions;
-  insufficient engagement.

Unless independent evidence establishes one of the closed FAIL codes, those

cases are ABSTAIN.

### OMITTED

OMITTED means a productive W/S task explicitly required the target Unit and

trustworthy evidence proves the Unit was absent.

The only v1 OMITTED reason is:

```
target_absent
```

OMITTED is lifecycle-inert.

R/L normal comprehension tasks do not use OMITTED merely because the learner

response does not repeat the Unit.

### ABSTAIN

ABSTAIN means the system cannot make a trustworthy lexical PASS/FAIL decision.

Closed v1 ABSTAIN reason codes are:

```
off_topic
refusal
explicit_skip
no_response
insufficient_lexical_evidence
response_unintelligible
audio_unusable
transcription_uncertain
transcription_failed
semantic_uncertainty
reviewer_rejected
invalid_artifact
infrastructure_failure
```

Machine, infrastructure, parsing, reviewer, transcription, or semantic

uncertainty must never be converted into learner FAIL.

### D35 lifecycle-field rule

PASS and FAIL producer JUDGE records carry the complete D35 set:

```
assessment_id
stimulus_ref
novel
```

`novel` is the truthful D55 result and may be either `True` or `False`.

OMITTED and ABSTAIN JUDGE records contain zero fields from that set.

A producer must never emit a partial D35 set.

Therefore:

```
PASS/FAIL + novel=True
    -> lifecycle-eligible under existing D35/T9 rules

PASS/FAIL + novel=False
    -> parsed assessment evidence, but lifecycle-inert under T9

OMITTED/ABSTAIN
    -> audit-only and not parsed as LifecycleAssessment
```

This decision does not alter D35 or T9.

**Reason:** `novel=True, passed=False` can reset a mastery streak and gate

RELAPSE. Only genuine lexical failure may carry that meaning. Omission,

off-task behavior, and machine uncertainty must not silently become lifecycle

degradation.

**Out of scope:** numeric scores, confidence-to-grade mapping, band scores, and

generic task-compliance grading.

## D54 — Cognitive stimulus, artifact, response, attempt, and assessment identity

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11, T12

All T11/T12 content identities use full lowercase SHA-256 over canonical JSON

unless an exact raw-byte digest is explicitly required.

Canonical JSON is:

```
json.dumps(
    value,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Every identity projection contains an explicit domain/version so identities

from different semantic namespaces cannot collide by interpretation.

### Canonical stimulus text

For cognitive text identity only:

1.  Unicode normalize with NFKC;
2.  normalize line endings;
3.  collapse Unicode whitespace runs to one ASCII space;
4.  trim surrounding whitespace;
5.  preserve case;
6.  preserve punctuation.

Display styling, line wrapping, HTML presentation, audio encoding, voice, and

container metadata are not cognitive content unless the task kind explicitly

claims those features as the assessed construct.

### Cognitive stimulus identity

The stable cognitive identity is:

```
stimulus:v1:<full-lowercase-sha256>
```

The canonical projection is channel-specific.

R:

```
domain
v
unit_key
channel = R
task_kind = reading_comprehension
canonical_passage
canonical_question
```

L:

```
domain
v
unit_key
channel = L
task_kind = listening_comprehension
canonical_spoken_script
canonical_question
```

W:

```
domain
v
unit_key
channel = W
task_kind = written_production
canonical_production_prompt
canonical_semantic_constraints
```

S:

```
domain
v
unit_key
channel = S
task_kind = spoken_production
canonical_production_prompt
canonical_semantic_constraints
```

The exact field names used in code may be frozen as machine constants later,

but they must preserve this semantic projection exactly.

For L, rendering the same spoken script with another voice does not create a

new cognitive stimulus.

For R/W/S, trivial layout or formatting changes do not create a new cognitive

stimulus.

A future task where voice, accent, formatting, or another presentation feature

is itself the assessed construct requires another task kind and identity

projection.

### Stimulus artifact identity

The exact rendered artifact is separate:

```
sha256:<SHA256(exact artifact bytes)>
```

This may distinguish different audio renderings or displayed artifacts without

inflating cognitive novelty.

### Session and item identity

T12 freezes an immutable session manifest before presentation.

Each attempt references:

```
session_id
item_ordinal
```

`session_id` is a stable explicit identity of that persisted session manifest.

The same manifest must reuse the same `session_id` on resume.

`item_ordinal` is an actual non-negative integer unique within that session

manifest.

Changing `session_id` or `item_ordinal` merely to reprocess an existing

captured learner response is forbidden.

The exact future creation policy for new session IDs belongs to T12 session

implementation; once assigned, the identity is immutable.

### Attempt identity

```
attempt:v1:<full-lowercase-sha256>
```

is SHA-256 of canonical JSON containing:

```
domain
v
producer
producer_version
session_id
item_ordinal
unit_key
channel
presented_stimulus_ref
```

A deliberate later re-presentation receives a new attempt identity but retains

the same cognitive stimulus identity.

### Response artifact identity

Text response identity is:

```
sha256:<SHA256(exact captured UTF-8 learner-response bytes)>
```

Speech response identity is:

```
sha256:<SHA256(exact immutable raw learner-audio bytes)>
```

For speech, none of these may enter learner-response identity:

-  transcript text;
-  transcript digest;
-  STT model;
-  decoder settings;
-  STT confidence;
-  semantic verdict.

Those are processing provenance only.

### Assessment identity

For T12 assessment producer v1:

```
assessment_id == attempt_id
stimulus_ref == presented_stimulus_ref
```

Rejudging or retranscribing one attempt therefore cannot create an independent

mastery observation.

Changing any of these under one attempt identity is a conflict:

-  Unit;
-  channel;
-  cognitive stimulus;
-  captured response artifact;
-  outcome;
-  semantic payload.

Judge identity, model version, rubric version, prompt version, verdict,

timestamp, and STT output are excluded from `assessment_id`.

A changed verdict for an existing attempt is not a new assessment. T12 v1

fails closed. Automatic supersession and revision history are not defined.

**Reason:** Independent mastery evidence must correspond to genuinely distinct

learner attempts and cognitive stimuli, not model upgrades, retranscription,

rerendering, formatting changes, or repeated judging of the same response.

**Out of scope:** automatic supersession, revision counters, pronunciation

stimulus identity, and cross-task identity equivalence.

## D55 — Durable exposure reservation and crash-safe novelty

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T12, T11 lifecycle evidence

Novelty is an exposure-history fact, not a semantic-model opinion.

T12 owns a separate append-only exposure ledger at the explicit sibling path:

```
t12-exposures.jsonl
```

The path is supplied explicitly at runtime alongside the EventLog path.

The ledger is not:

-  a vocabulary registry;
-  a Unit database;
-  lifecycle state;
-  a replacement for Anki;
-  a replacement for EventLog outcomes.

Its single authority is:

> which assessment-attempt stimulus identities have consumed novelty.

EventLog remains authoritative for assessment outcomes.

### Exposure record

Each exposure reservation contains exactly:

```
v
producer
producer_version
reserved_at
attempt_id
session_id
item_ordinal
unit_key
channel
presented_stimulus_ref
stimulus_artifact_ref
```

The v1 producer is the frozen T12 assessment producer defined by D57.

`reserved_at` is a normalized UTC ISO-8601 timestamp with explicit `+00:00`

offset. It is audit metadata and does not define slot identity or novelty

ordering.

`item_ordinal` is an actual non-negative integer; Boolean is not accepted.

All strings required as identities are non-empty and must satisfy their frozen

identity grammar.

### Ledger serialization

Each record is serialized as one UTF-8 JSONL line using exactly:

```
json.dumps(
    record,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
)
```

followed by exactly one newline.

Before append:

-  the complete record is validated;
-  JSON serialization must succeed;
-  the current ledger history must be valid.

Append then performs:

```
write complete line
flush
fsync
```

and exact readback verification of the appended record.

The ledger is append-only.

T12 v1 never:

-  rewrites;
-  deletes;
-  truncates;
-  automatically repairs;
-  silently ignores an interior malformed record.

Malformed, duplicate, conflicting, undecodable, or structurally invalid

history fails closed.

### Slot identity

Exposure-ledger slot identity is exactly:

```
producer
producer_version
attempt_id
```

A duplicate physical slot fails closed even when byte-for-byte or semantically

identical.

Reusing an attempt ID with changed Unit, channel, session, ordinal, cognitive

stimulus, or rendered artifact is a conflict.

Physical ledger order is authoritative reservation order. `reserved_at` is not

used to reorder history.

### Reserve before display

The required sequence is:

```
1. validate complete ledger
2. validate current session / attempt / stimulus identities
3. construct and serialize exact reservation
4. append reservation
5. flush + fsync
6. exact readback
7. issue one-use in-memory display permit
8. only then display the stimulus
```

If reservation or readback fails, the stimulus must not be shown.

An already durable reservation is not a reusable display permit after restart.

After restart:

-  if an immutable learner response for that same attempt was already captured,
   processing may resume without redisplaying the stimulus;
-  otherwise that attempt is abandoned;
-  a later presentation uses a new attempt ID.

The existing reservation remains and conservatively consumes novelty.

### Crash asymmetry

```
crash before durable reservation:
    no display was authorized

crash after reservation but before display:
    false non-novel is possible
    this is conservative and safe

crash after display:
    exposure evidence already exists

crash after response capture:
    same attempt may resume without redisplay
```

False non-novel is preferred over false novel because the former loses an

assessment opportunity while the latter can create false independent mastery

evidence.

### Novelty rule

For the current attempt:

```
novel == True
```

iff all are true:

1.  the complete exposure ledger is valid;
2.  the current attempt has exactly one verified durable reservation;
3.  no earlier physical ledger record with a different `attempt_id` has the
    same:

```
unit_key
channel
presented_stimulus_ref
```

The current attempt's own reservation does not make itself non-novel.

Every reservation consumes novelty, including attempts that later become:

-  PASS;
-  FAIL;
-  OMITTED;
-  ABSTAIN;
-  interrupted;
-  never displayed after reservation;
-  never represented by a final EventLog outcome.

JUDGE history is not a substitute for exposure history.

Every T12 JUDGE/SPEAK event must cross-check to exactly one compatible durable

exposure reservation. A ledger reservation is allowed to have no final event,

because crash/abandonment is valid.

If the ledger is missing, corrupt, ambiguous, or incomplete:

```
novel=True is forbidden
no new lifecycle-bearing JUDGE may be emitted
```

T12 does not convert that uncertainty into learner failure.

**Reason:** A stimulus may already influence the learner even when no outcome

event was ever written. Pre-display durable reservation makes false novelty

impossible across ordinary crash boundaries.

**Out of scope:** distributed locking, concurrent session writers, automatic

ledger repair, exposure deletion, and a second Unit registry.

## D56 — Speech transcription evidence and SPEAK semantics

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11, T12

S v1 assesses lexical use through an immutable learner audio artifact plus a

human-verified transcript.

Raw audio is never sent directly to an unscripted semantic judge.

Speech recognition is local. Paid Azure/OpenAI/Anthropic speech APIs are not

part of the active v1 architecture.

### Transcription status

The closed values are:

```
SUCCESS
UNCERTAIN
FAILED
```

#### SUCCESS

`SUCCESS` means all are true:

-  immutable raw learner audio exists;
-  local STT produced a transcript candidate;
-  a human verifier listened to that exact audio artifact;
-  the human approved an exact transcript as sufficiently faithful for lexical
   and semantic assessment;
-  the approved transcript digest is frozen;
-  verifier provenance is frozen.

Only a SUCCESS transcript may enter D19 or semantic assessment.

If D19 finds the Unit absent from a SUCCESS transcript:

```
outcome = OMITTED
reason_code = target_absent
```

#### UNCERTAIN

`UNCERTAIN` means audio is available but trustworthy transcription or reliable

target presence/absence cannot be established.

Examples:

-  unclear audio;
-  ambiguous target realization;
-  STT/human disagreement;
-  verifier cannot approve the candidate transcript.

Required outcome:

```
outcome = ABSTAIN
reason_code = transcription_uncertain
```

D19 must not infer target absence.

#### FAILED

`FAILED` means no usable transcript can be produced because of an artifact or

technical failure.

Applicable audit-only reasons are:

```
transcription_failed
audio_unusable
infrastructure_failure
```

Required outcome is ABSTAIN.

D19 must not infer target absence.

### Confidence and multiple decodes

None of the following alone can establish lexical absence:

-  one STT output;
-  a model confidence threshold;
-  agreement between multiple automatic decodes;
-  absence of the target token in an unverified transcript.

STT confidence may be retained only in a referenced diagnostic artifact; it

does not become lifecycle truth.

### Stored transcript

For SUCCESS:

```
SPEAK.transcript = exact human-approved transcript
```

For UNCERTAIN or FAILED:

```
SPEAK.transcript = ""
```

No unverified transcript is promoted to the top-level accepted transcript

field.

### SPEAK.passed

For every T12 SPEAK event:

```
passed == (outcome == "PASS")
```

`SPEAK.passed` means the final accepted semantic speaking outcome is PASS.

It does not mean:

-  STT executed;
-  transcription succeeded;
-  target presence alone;
-  readable audio alone;
-  pronunciation quality.

Transcription status is represented separately.

SPEAK remains lifecycle-inert under D35. A speaking result affects lifecycle

only through a companion qualifying JUDGE.

### Evidence order

For a normal S attempt:

```
immutable raw audio
    ->
local STT
    ->
human transcript verification
    ->
D19 presence gate
    ->
semantic assessment when target present
    ->
human approval of exact semantic proposal
    ->
T12 SPEAK/JUDGE producer planning
```

For an S attempt where no immutable raw audio was ever captured, T12 emits

neither SPEAK nor JUDGE. Its durable D55 reservation remains and consumes

novelty.

**Reason:** Transcript absence is not proof of learner omission unless the

transcript itself is trustworthy. This prevents STT error from becoming

learner failure and narrows v1 to evidence it can honestly support.

**Out of scope:** pronunciation scoring, prosody, accent grading, automatic

confidence-to-outcome thresholds, and paid cloud speech APIs.

## D57 — T12 assessment producer, closed payloads, idempotency, and crash recovery

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11, T12

The v1 assessment producer namespace is exactly:

```
producer = "t12-assessment"
producer_version = 1
```

Producer identity is distinct from assessor identity.

`model_id` / `model_version` identify the authority responsible for the stored

outcome under the historical generic JUDGE/SPEAK contract. They do not replace

the T12 producer namespace.

A human-mediated semantic bridge must record the model identity exactly as

available. If the UI does not expose a stable underlying build identifier, the

producer must record a frozen truthful "version unavailable from UI" sentinel

defined by contract rather than inventing a model version.

### Generic EventLog compatibility

Generic `EVENT_SCHEMA_VERSION` remains unchanged.

Generic historical JUDGE and SPEAK events remain readable.

The generic EventLog continues validating only its existing unconditional

structural contract.

T12 producer-specific validation owns all rules in this decision.

No EventLog schema-version bump is required merely because T12 adds closed

producer fields.

T12 v1 is the only normal producer permitted to create new lifecycle-bearing

JUDGE records. Application code must not bypass the T12 producer and append a

new D35-bearing JUDGE directly through `EventLog.log()`.

### Outcome slot identity

T12 EventLog slot identity is exactly:

```
producer
producer_version
event_type
attempt_id
```

where event type is:

```
JUDGE
SPEAK
```

Changing Unit, channel, stimulus, response artifact, outcome, provenance, or

other semantic content under the same slot is a conflict, not a new slot.

Duplicate historical slots fail closed even when payloads are identical.

### Common JUDGE payload

Every T12 JUDGE contains:

```
channel
passed
model_id
model_version
producer
producer_version
attempt_id
presented_stimulus_ref
outcome
authority_kind
provenance
```

`authority_kind` is exactly one of:

```
semantic_model
deterministic_gate
policy
human_reviewer
```

`response_artifact_ref` is additionally required whenever an immutable learner

response exists.

It is prohibited, rather than stored as null or empty, for pre-capture cases

such as:

```
no_response
invalid_artifact
pre-capture infrastructure_failure
```

Outcome-specific JUDGE closure is:

#### PASS

Additional required:

```
response_artifact_ref
assessment_id
stimulus_ref
novel
```

Prohibited:

```
failure_code
reason_code
```

#### FAIL

Additional required:

```
response_artifact_ref
assessment_id
stimulus_ref
novel
failure_code
```

Prohibited:

```
reason_code
```

#### OMITTED

Additional required:

```
response_artifact_ref
reason_code = target_absent
```

Prohibited:

```
assessment_id
stimulus_ref
novel
failure_code
```

#### ABSTAIN

Additional required:

```
reason_code
```

`response_artifact_ref` is present iff an immutable response exists.

Prohibited:

```
assessment_id
stimulus_ref
novel
failure_code
```

For PASS/FAIL:

```
assessment_id == attempt_id
stimulus_ref == presented_stimulus_ref
passed == (outcome == PASS)
```

OMITTED and ABSTAIN contain zero D35 fields.

Unknown top-level fields are forbidden.

### Common SPEAK payload

Every T12 SPEAK contains exactly the common fields:

```
audio_path
transcript
passed
model_id
model_version
channel
producer
producer_version
attempt_id
presented_stimulus_ref
response_audio_ref
outcome
authority_kind
provenance
```

and:

```
channel == S
passed == (outcome == PASS)
```

Outcome additions are:

```
PASS:
    no failure/reason field

FAIL:
    failure_code

OMITTED:
    reason_code = target_absent

ABSTAIN:
    reason_code
```

No SPEAK payload may contain:

```
assessment_id
stimulus_ref
novel
```

Unknown top-level fields are forbidden.

### Companion consistency

For one S attempt, SPEAK and JUDGE must agree exactly on:

-  producer;
-  producer version;
-  attempt ID;
-  Event envelope Unit key;
-  channel;
-  presented stimulus reference;
-  raw response-audio identity;
-  outcome;
- `passed`;
-  applicable failure or reason code.

A lifecycle-relevant S JUDGE without the corresponding exact SPEAK evidence is

invalid producer history.

### Closed provenance

`provenance` is a closed object.

Its only allowed stage keys are:

```
presence_gate
transcription
semantic_judge
human_review
policy
```

No unknown stage key is allowed.

Fields from a stage that was not invoked must not be invented.

#### presence\_gate

Exact fields:

```
gate_id
gate_version
target_present
```

`target_present` is an actual Boolean.

#### semantic\_judge

T11 v1 uses the ChatGPT Plus semantic bridge and therefore this stage contains

exactly:

```
protocol_id
protocol_version
assessor_id
assessor_version
rubric_id
rubric_version
prompt_id
prompt_version
request_digest
response_digest
```

`request_digest` identifies the exact deterministic request artifact.

`response_digest` identifies the exact imported structured semantic proposal.

These fields do not enter `assessment_id`.

#### human\_review

Exact fields:

```
reviewer_id
reviewer_version
decision
```

with:

```
decision = APPROVE | REJECT
```

APPROVE accepts the exact semantic proposal.

REJECT produces ABSTAIN and does not edit the proposal.

#### policy

Exact fields:

```
policy_id
policy_version
```

#### transcription

`transcription` is a closed union.

SUCCESS:

```
status = SUCCESS
stt_model_id
stt_model_version
decoder_version
stt_output_ref
approved_transcript_ref
verifier_id
verifier_version
```

UNCERTAIN:

```
status = UNCERTAIN
stt_model_id
stt_model_version
decoder_version
stt_output_ref
verifier_id
verifier_version
uncertainty_code
```

FAILED before STT invocation:

```
status = FAILED
failure_code
```

FAILED after STT invocation:

```
status = FAILED
stt_model_id
stt_model_version
decoder_version
failure_code
```

### Required provenance combinations

R/L PASS or FAIL:

```
semantic_judge
human_review
```

W PASS or FAIL:

```
presence_gate
semantic_judge
human_review
```

S PASS or FAIL:

```
transcription
presence_gate
semantic_judge
human_review
```

W OMITTED:

```
presence_gate
```

S OMITTED:

```
transcription
presence_gate
```

S transcription ABSTAIN:

```
transcription
policy
```

Semantic-uncertainty ABSTAIN:

```
all successfully invoked prerequisites
semantic_judge
policy
```

Reviewer-rejected ABSTAIN:

```
all successfully invoked prerequisites
semantic_judge
human_review
policy
```

Other audit-only ABSTAIN outcomes contain only the stages actually needed to

establish the reason plus `policy`.

### Complete preflight

Before the first EventLog append in a producer run, T12 must:

1.  read and fully validate the complete D55 exposure ledger;
2.  read the complete EventLog;
3.  validate every historical `t12-assessment` JUDGE/SPEAK payload;
4.  validate every T12 event-to-exposure-ledger correspondence;
5.  detect every duplicate slot;
6.  detect every conflicting slot;
7.  validate every planned producer payload;
8.  classify every planned slot as missing, exact, or conflicting.

Nothing is appended unless the entire preflight succeeds.

### Text-channel rerun

For non-speech JUDGE:

```
missing slot
    -> append

one exact slot
    -> skip

same slot with different payload
    -> conflict

duplicate historical slot
    -> fail closed even if identical
```

Rejudging the same attempt with a changed result conflicts.

T12 v1 performs no overwrite or automatic supersession.

### Speech partial-history state machine

For one S attempt:

```
neither SPEAK nor JUDGE:
    append SPEAK
    fsync through EventLog
    then append JUDGE

exact SPEAK + missing JUDGE:
    legal crash-resume state
    append only JUDGE

missing SPEAK + existing JUDGE:
    forbidden producer corruption
    append nothing

both exact:
    exact rerun
    append zero

SPEAK same slot, differing payload:
    conflict
    append zero

JUDGE same slot, differing payload:
    conflict
    append zero

duplicate SPEAK slots:
    fail closed even if identical

duplicate JUDGE slots:
    fail closed even if identical
```

A JUDGE-append failure after a durable SPEAK leaves the sole legal incomplete S

history.

No automatic retry occurs in the same failed operation. A later explicit run

performs complete preflight and resumes by appending only the missing exact

JUDGE.

The reverse partial state is never auto-repaired.

### No lifecycle mutation

T12 emits evidence only.

It never:

-  writes `state_*`;
-  emits STATE;
-  calls Anki lifecycle mutation;
-  suspends or unsuspends cards;
-  invokes T9 state materialization.

**Reason:** D35 makes JUDGE load-bearing lifecycle evidence. Producer identity,

payload closure, exposure correspondence, and idempotent crash recovery must be

validated before that evidence enters the append-only history.

**Out of scope:** producer supersession, event deletion, concurrent writers,

automatic retry loops, EventLog schema v2, and direct paid-model APIs.

## D58 — T11/T12 invariant probes and lifecycle enablement gate

**Date:** 2026-08-23

**Status:** Accepted

**Blocks:** T11 closure, T12 assessment producer

T11/T12 is not lifecycle-enabled merely because code compiles or unit tests

cover schema validation.

Before real lifecycle-bearing assessment production, the complete invariant

probe suite must pass.

The probes are not an estimate of general language-assessment accuracy. They

are a protocol-validity and fail-silent safety gate.

### Mandatory semantic anchors

At minimum, the probe set must include:

```
R:
    correct contextual paraphrase omitting target -> PASS
    explicit wrong target meaning -> FAIL/wrong_meaning
    off-topic but interpretable response -> ABSTAIN

L:
    correct contextual interpretation without target repetition -> PASS
    explicit wrong interpretation -> FAIL/wrong_interpretation

W:
    correct target use -> PASS
    target absent -> OMITTED
    semantic misuse -> FAIL/semantic_misuse
    collocational misuse -> FAIL/collocation_misuse

S:
    verified transcript + correct use -> PASS
    verified transcript + genuine misuse -> corresponding FAIL
    unverified STT omission -> ABSTAIN
    human-verified target absence -> OMITTED
```

No semantic anchor may silently invert the construct:

-  correct evidence must not become FAIL;
-  genuine closed-code lexical failure must not become PASS;
-  omission must not become FAIL;
-  uncertainty must not become FAIL.

A human reviewer rejection remains ABSTAIN and therefore cannot hide an unsafe

PASS/FAIL as accepted evidence.

A channel is not lifecycle-enabled until its mandatory anchors are accepted

with the required exact outcomes.

### Identity and novelty probes

Mandatory probes include:

-  same cognitive L script rendered by different voices -> same stimulus ref;
-  trivial formatting differences excluded by D54 -> same cognitive identity;
-  genuinely different cognitive stimulus -> different stimulus ref;
-  same raw learner audio under different STT transcripts -> same attempt /
   assessment identity;
-  same attempt rejudged -> same assessment ID;
-  prior OMITTED stimulus -> later same stimulus is non-novel;
-  prior ABSTAIN stimulus -> later same stimulus is non-novel;
-  interrupted reserved attempt -> later same stimulus is non-novel.

### Exposure crash probes

Mandatory probes include:

```
crash before reservation durability:
    stimulus not authorized for display

crash after reservation but before display:
    reservation remains
    novelty consumed

crash after display but before outcome:
    reservation remains
    novelty consumed

restart with reservation but no captured response:
    existing attempt not redisplayed

restart with immutable captured response:
    same attempt may resume without redisplay
```

### Producer-history probes

Mandatory probes include:

-  exact text-JUDGE rerun appends zero;
-  same text-JUDGE slot with changed payload conflicts;
-  duplicate identical historical JUDGE slot fails closed;
-  arbitrary producer payload extra field is rejected;
-  partial D35 field set is rejected;
-  lifecycle-bearing JUDGE without compatible exposure receipt is rejected;
-  legal SPEAK-only partial history resumes with only missing JUDGE;
-  JUDGE-only S history fails closed;
-  duplicate identical SPEAK slot fails closed;
-  changed retranscription under same attempt conflicts if it changes the
   stored producer payload;
-  changed rejudgment under same attempt conflicts;
-  exact SPEAK+JUDGE rerun appends zero.

### Speech evidence probes

Mandatory probes include:

-  raw STT transcript without human verification cannot establish omission;
-  STT confidence threshold cannot establish omission;
-  UNCERTAIN transcription -> ABSTAIN;
-  FAILED transcription -> ABSTAIN;
-  human-approved SUCCESS transcript may enter D19;
-  human-confirmed target absence -> OMITTED;
-  SPEAK `passed` always equals semantic PASS, not transcription success.

### Acceptance rule

Every safety, identity, idempotency, crash-recovery, payload-closure, and

novelty invariant above must pass 100%.

One violation is NO-GO.

No aggregate score may hide:

-  false novel evidence;
-  duplicate independent evidence;
-  omission recorded as FAIL;
-  uncertainty recorded as FAIL;
-  cross-channel evidence;
-  malformed producer history;
-  JUDGE-only speech lifecycle evidence.

The semantic anchors are deliberately small adversarial probes. Passing them

does not prove general assessment validity, accuracy, agreement with an

examiner, or population-level calibration.

After lifecycle enablement, real assessment quality must remain reviewable and

future calibration may narrow or supersede the semantic rubric through an

explicit decision. Existing append-only evidence is never silently regraded.

If a semantic classifier or metric fails its invariant direction, it is removed

from lifecycle use rather than compensated for with an arbitrary numeric

threshold.

**Reason:** T11/T12 sits directly upstream of D35 lifecycle evidence. The

primary acceptance criterion is absence of unsafe fail-silent behavior, not a

small-sample aggregate accuracy score.

**Out of scope:** statistical validation claims from the probe set, automated

rubric optimization, model fine-tuning, and retroactive regrading.

## D59 — T11 human-mediated semantic assessment bridge artifacts

**Date:** 2026-08-24  
**Status:** Accepted  
**Blocks:** T11.3a, T11.3b, T11.3c, T11.4, T12 semantic-assessment provenance  
**Preserves:** D51–D58 unchanged

D59 freezes the deterministic artifact protocol used by the T11 v1 human-mediated semantic bridge. It does not move session, attempt, cognitive-stimulus, response-artifact, novelty, EventLog, or lifecycle ownership from T12 into T11.

T11 v1 continues to use the human-mediated flow frozen by D51:

```text
deterministic semantic request artifact
    ->
human submits through ChatGPT Plus
    ->
structured semantic proposal artifact
    ->
strict deterministic import
    ->
human APPROVE or REJECT of the exact proposal
```

Python performs no paid LLM API call and no browser/UI automation.

### Layer separation

The generic T11 result contract remains unchanged:

```text
T11AssessmentResult
validate_t11_assessment_result(...)
```

`T11AssessmentResult` remains the final five-field result model:

```text
unit_key
channel
outcome
failure_code
reason_code
```

`semantic_rationale`, protocol/rubric/prompt text, assessor metadata, and review metadata never become fields on `T11AssessmentResult`.

The semantic proposal is a separate immutable artifact. Its restricted outcome/reason vocabulary is enforced by the semantic-proposal importer and does not narrow the generic `ASSESSMENT_OUTCOMES` or `ASSESSMENT_ABSTAIN_REASON_CODES` contracts.

### Artifact namespaces and schema versions

The frozen artifact discriminator/version pairs are:

```text
semantic request:
    artifact = vocab.t11.semantic-request
    v = 1

semantic proposal:
    artifact = vocab.t11.semantic-response
    v = 1

human review:
    artifact = vocab.t11.human-review
    v = 1
```

Every artifact `v` is an actual integer. Boolean is not accepted as an integer version.

### Protocol, rubric, and prompt labels

The frozen labels are:

```text
protocol:
    id = t11-semantic-assessment
    version = 1

rubric:
    id = d52-d53-lexical-assessment
    version = 1

prompt:
    id = t11-semantic-bridge
    version = 1
```

The three `version` values above are positive actual integers. Boolean is not accepted.

These ID/version pairs are descriptive version labels, not standalone content identities. D59 intentionally does not claim that one ID/version pair globally maps to one immutable historical text.

Every semantic request embeds the exact protocol, rubric, and prompt text used for that request. Those texts are explicit human-owned inputs, not hidden registry lookups. They are digest-significant. If a text changes while its descriptive label remains the same, the request digest changes. No second registry and no EventLog scan is introduced merely to enforce a historical label-to-text mapping.

### Canonical JSON

Every D59 content digest uses the D54 canonical JSON formula exactly:

```python
json.dumps(
    value,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Canonical serialization emits UTF-8 without a BOM.

Canonicalization changes JSON object-key ordering and insignificant transport whitespace only. String contents are preserved exactly. T11 does not trim, casefold, Unicode-normalize, or otherwise repair digest-significant artifact strings.

D54 cognitive-stimulus text normalization remains separate and T12-owned. A T11 request digest is the identity of the exact semantic-assessment request artifact, not the T12 cognitive-stimulus identity.

### Digest representation

`request_digest` and `response_digest` are exactly full lowercase SHA-256 digests represented as:

```text
64 lowercase hexadecimal characters
```

They do not carry the `sha256:` prefix.

The `sha256:` prefix remains reserved for contracts that explicitly define an artifact/reference grammar such as `*_ref`. D59 does not change any existing FORGE, T8, T9, T10, or T12 digest/reference format.

### Semantic request — exact closed schema

A semantic request is a top-level JSON object with exactly these keys:

```text
artifact
v
protocol
rubric
prompt
unit
task
```

No unknown or missing top-level field is allowed.

`artifact` and `v` must equal the frozen semantic-request discriminator/version.

#### Versioned instruction objects

Each of `protocol`, `rubric`, and `prompt` is an object with exactly:

```text
id
version
text
```

For v1:

- `id` is the exact frozen label for that object;
- `version` is the exact frozen positive integer version;
- `text` is a required non-whitespace string;
- `text` is preserved verbatim and is digest-significant.

No null or omitted representation is allowed.

#### Unit object

`unit` contains exactly:

```text
unit_key
lemma
unit_type
definition_en
```

Rules:

- `unit_key` reuses the existing `UNIT_KEY_PATTERN` authority;
- `unit_type` reuses the existing `UNIT_TYPE_VALUES` authority;
- `lemma` is a required non-whitespace string;
- `definition_en` is a required non-whitespace string;
- no field is trimmed, normalized, repaired, or invented during serialization/import.

These Unit facts make the semantic request self-contained. The semantic bridge must not require hidden Anki or registry access to discover the target form or intended sense.

#### Task union

`task` is exactly one of four closed channel-specific objects.

Reading:

```text
channel
task_kind
passage
question
learner_response
```

with exact semantic values:

```text
channel = R
task_kind = reading_comprehension
```

Listening:

```text
channel
task_kind
spoken_script
question
learner_response
```

with:

```text
channel = L
task_kind = listening_comprehension
```

Writing:

```text
channel
task_kind
production_prompt
semantic_constraints
learner_response
```

with:

```text
channel = W
task_kind = written_production
```

Speaking:

```text
channel
task_kind
production_prompt
semantic_constraints
approved_transcript
```

with:

```text
channel = S
task_kind = spoken_production
```

Every task string other than the closed `channel`/`task_kind` values is required to be a non-whitespace string and is preserved verbatim.

`task_kind` is not an independently caller-controlled semantic decision. It is derived from the frozen `ASSESSMENT_TASK_KIND_BY_CHANNEL` mapping. A serializer emits the exact pair for the chosen task variant. A parser must reject a mismatched pair and must not repair one value from the other.

For example:

```text
channel = R
task_kind = spoken_production
```

is invalid and fails closed.

#### Semantic constraints

For W and S, `semantic_constraints` is a required `str` describing the semantic constraints of the learner production task, including the intended sense/context requirement that the learner's production must satisfy.

It is part of the production stimulus. It is not:

- the scoring rubric;
- the closed D53 failure-code inventory;
- reviewer instructions;
- hidden assessor-only grading criteria.

It must be non-whitespace, is preserved verbatim, and is digest-significant.

#### Channel evidence boundary

R semantic assessment consumes:

```text
passage
question
learner_response
```

L semantic assessment consumes:

```text
spoken_script
question
learner_response
```

The `spoken_script` is assessor-visible evidence of the content that was heard; ordinary learner-facing L presentation remains governed by D52 and does not reveal written stimulus text to the learner.

W semantic assessment consumes:

```text
production_prompt
semantic_constraints
learner_response
```

A W semantic request may be constructed only after the D19 presence gate has already established target presence. D59 does not implement the gate and does not treat presence as semantic correctness.

S semantic assessment consumes:

```text
production_prompt
semantic_constraints
approved_transcript
```

Only the exact D56 human-approved SUCCESS transcript may enter the S semantic request. Raw audio, audio paths, STT candidates, STT confidence, and transcription provenance do not enter the request.

A semantic request is never constructed for W/S target absence; that path produces deterministic `OMITTED / target_absent` before semantic assessment under D52/D53/D56.

Likewise, `refusal`, `explicit_skip`, and `no_response` are resolved before semantic assessment and do not require a semantic request.

### Request digest

`request_digest` is:

```text
SHA256(canonical_json_bytes(the complete validated semantic-request object))
```

represented as full lowercase bare 64-hex.

The request object contains no `request_digest` field, so the digest is not self-referential.

Every field of the closed semantic-request schema is digest-significant.

Changing exact protocol/rubric/prompt text, Unit facts, task content, learner evidence, discriminator, or version changes the request digest.

### Semantic proposal — exact closed schema

The structured semantic proposal returned through the human-mediated ChatGPT Plus workflow is a top-level JSON object containing exactly:

```text
artifact
v
request_digest
outcome
failure_code
reason_code
semantic_rationale
```

No unknown or missing field is allowed.

`artifact` and `v` must equal the frozen semantic-response discriminator/version.

`request_digest` must be a valid bare lowercase 64-hex digest and must equal the digest independently recomputed from the exact supplied semantic request.

`outcome`, `failure_code`, `reason_code`, and `semantic_rationale` are semantic proposal fields. The semantic model does not supply T12 identity, novelty, lifecycle, producer, or EventLog fields.

#### Proposal outcome subset

A semantic proposal may emit only:

```text
PASS
FAIL
ABSTAIN
```

It may never emit `OMITTED`.

`OMITTED / target_absent` is owned by the deterministic D19 presence-gate path for productive W/S tasks before semantic assessment.

#### Failure and reason representation

`failure_code` and `reason_code` are always present and are always strings.

When a field does not apply, its exact value is:

```text
""
```

Null, omission, whitespace substitutes, and coercion are forbidden.

Proposal combinations are:

PASS:

```text
failure_code = ""
reason_code = ""
```

FAIL:

```text
failure_code = one closed D53 code valid for the request-derived channel
reason_code = ""
```

ABSTAIN:

```text
failure_code = ""
reason_code = one semantic-assessor-owned ABSTAIN reason
```

The importer constructs the corresponding request-derived `T11AssessmentResult` and reuses `validate_t11_assessment_result()` rather than duplicating generic channel/outcome/failure/reason validation.

#### Semantic-assessor-owned ABSTAIN reasons

The semantic proposal may use exactly these ABSTAIN reasons:

```text
off_topic
insufficient_lexical_evidence
response_unintelligible
semantic_uncertainty
```

It may not emit:

```text
refusal
explicit_skip
no_response
audio_unusable
transcription_uncertain
transcription_failed
reviewer_rejected
invalid_artifact
infrastructure_failure
```

Ownership remains:

- `refusal`, `explicit_skip`, `no_response`: pre-semantic response/session handling;
- audio/transcription reasons: D56 transcription path;
- `reviewer_rejected`: human-review materialization;
- `invalid_artifact`, `infrastructure_failure`: deterministic/policy layers.

An out-of-subset reason is an invalid semantic proposal. The importer rejects it; it does not remap, repair, or reinterpret it.

#### Semantic rationale

`semantic_rationale` is required for every semantic proposal.

It must be a non-whitespace string, is preserved verbatim, and is digest-significant.

This field exists because D51 requires the human reviewer to APPROVE or REJECT the exact semantic proposal without rewriting its rationale or any other semantic field.

T11 validates rationale presence and binding only. It does not deterministically judge rationale truth, quality, confidence, style, or persuasiveness.

### Response digest

`response_digest` is:

```text
SHA256(canonical_json_bytes(the complete validated semantic-proposal object))
```

represented as full lowercase bare 64-hex.

The semantic proposal contains no `response_digest` field, so the digest is not self-referential.

Equivalently, the complete response-digest projection contains exactly:

```text
artifact
v
request_digest
outcome
failure_code
reason_code
semantic_rationale
```

Assessor metadata is not part of `response_digest`.

Therefore identical semantic proposal content from two different assessors has the same `response_digest` while carrying different assessor provenance. This is correct. T11 has no history authority and does not classify such cases as duplicates or conflicts. T12 producer-history rules remain authoritative for conflicts involving one persisted attempt.

### Assessor metadata capture

The semantic model is not trusted to self-certify its own UI model/build metadata inside the semantic proposal.

Assessor metadata is captured separately by the human-mediated workflow and supplied explicitly to strict import:

```text
assessor_id
assessor_version
```

`assessor_id` means the exact model label visibly available in the ChatGPT UI for the semantic-assessment run.

Rules:

- type is `str`;
- length is 1 through 128 Unicode code points;
- value must equal `value.strip()`;
- no Unicode code point with general category `Cc` is allowed;
- otherwise the visible label is preserved verbatim;
- it is not lowercased, slugified, normalized into a locally invented identifier, or replaced with `chatgpt-plus`;
- it is not replaced with the human reviewer identity.

If no model identity is visible at all, the run fails closed. T11 must not invent `assessor_id`.

`assessor_version` means the exact stable underlying model/build version visibly available in the UI when such a version is exposed.

It follows the same string hygiene rules as `assessor_id`.

If the UI exposes model identity but no stable underlying build/version, the exact frozen sentinel is:

```text
version-unavailable-from-ui
```

This concretizes the D57 truthful-unavailable-version rule and is not the T12 producer version.

Assessor metadata must be captured/validated before a human-mediated semantic run is accepted for later import so an otherwise valid manual batch is not silently given invented provenance after the fact.

### Semantic-judge provenance facts

T11 provides a pure derived semantic-judge provenance projection sufficient for later T12 producer construction.

Its exact D57 facts and sole sources are:

```text
protocol_id       <- request.protocol.id
protocol_version  <- request.protocol.version
rubric_id         <- request.rubric.id
rubric_version    <- request.rubric.version
prompt_id         <- request.prompt.id
prompt_version    <- request.prompt.version
assessor_id       <- externally captured assessor metadata
assessor_version  <- externally captured assessor metadata
request_digest    <- independently recomputed request digest
response_digest   <- independently recomputed response digest
```

These facts are derived from validated artifacts/metadata. They are not a second independently editable authority.

T11 does not construct the final EventLog `provenance` dictionary and does not write EventLog.

### Human review — exact closed schema

A human review artifact is a top-level JSON object containing exactly:

```text
artifact
v
response_digest
reviewer_id
reviewer_version
decision
```

No unknown or missing field is allowed.

`artifact` and `v` must equal the frozen human-review discriminator/version.

`response_digest` must be valid bare lowercase 64-hex and must equal the independently recomputed digest of the exact imported semantic proposal under review.

`reviewer_id` is a stable local pseudonymous identifier and must match the existing `SLUG_PATTERN` grammar exactly. D59 does not create a near-duplicate actor grammar.

`reviewer_version` is an actual positive integer. Boolean is not accepted. It versions the normative reviewer procedure/profile, beginning at `1`; it changes only when the normative APPROVE/REJECT review procedure changes. It is not a version number of the human being.

`decision` is exactly:

```text
APPROVE
REJECT
```

The review artifact carries no copied or editable semantic field. In particular it must not contain:

```text
outcome
failure_code
reason_code
semantic_rationale
unit_key
channel
```

or any replacement proposal object.

Changing any semantic proposal field changes `response_digest` and invalidates any review bound to the old digest.

A review that references another response fails closed for both APPROVE and REJECT.

### Review materialization boundary

T11.3c validates the exact human-review artifact and binding only.

T11.4 owns pure materialization of the reviewed result:

```text
APPROVE
    -> retain the exact request-derived semantic proposal result

REJECT
    -> audit-only T11AssessmentResult:
       outcome = ABSTAIN
       failure_code = ""
       reason_code = reviewer_rejected
```

REJECT never substitutes another semantic verdict and never edits the original proposal. The rejected proposal remains immutable audit evidence.

This materialization performs no T12 session, novelty, producer-history, EventLog, or lifecycle work.

### Strict deterministic import

D59 request/proposal/review import is fail-closed.

For artifact JSON inputs:

- the transport input is bytes;
- decoding is strict UTF-8;
- canonical serializers never emit a UTF-8 BOM;
- malformed JSON is rejected;
- duplicate object keys are rejected recursively;
- `NaN`, `Infinity`, and `-Infinity` are rejected;
- top-level arrays/scalars are rejected;
- exact closed keysets are required at every frozen object/union level;
- unknown and missing fields are rejected;
- null is rejected for every required v1 field;
- primitive types are exact and are never coerced;
- actual-integer checks reject Boolean;
- closed strings are exact and case-sensitive;
- non-whitespace checks may call `.strip()` only as a predicate; accepted content is never trimmed before storage or digesting;
- invalid artifacts are not repaired, normalized, retried, or silently downgraded into learner failure.

A request parser verifies the exact channel/task-kind pair and exact channel-specific task keyset.

A proposal importer verifies exact request-digest binding, the restricted semantic outcome/reason subset, and the generic request-derived `T11AssessmentResult` via `validate_t11_assessment_result()`.

A review importer verifies exact response-digest binding and exact review closure.

Importer/parsing failure does not itself invent a semantic verdict. Policy/T12 may later represent a validated audit-only `invalid_artifact` outcome under D53/D57 where appropriate.

### T12-owned fields prohibited from T11 semantic artifacts

No D59 semantic request, semantic proposal, or human review artifact may contain or generate T12-owned runtime/history identity fields, including:

```text
session_id
item_ordinal
attempt_id
assessment_id
stimulus_ref
presented_stimulus_ref
stimulus_artifact_ref
response_artifact_ref
response_audio_ref
novel
producer
producer_version
reserved_at
```

They also do not contain EventLog envelope fields such as runtime `ts`, `day`, or final event payloads.

For S they do not contain raw audio, audio paths, STT candidates, STT confidence, or transcription provenance. Only the D56-approved transcript enters the semantic request.

T11 semantic artifacts consume stimulus/evidence content without acquiring T12 ownership of runtime identity or history.

### Implementation sequencing

D59 freezes semantic protocol semantics, not implementation filenames.

Implementation remains split into small boundaries:

```text
T11.3a0
    shared canonical JSON / strict JSON parsing extraction only
    no T11 semantic constants or artifact semantics

T11.3a
    semantic request schema
    deterministic serialization/import
    request digest

T11.3b
    strict semantic-proposal import
    external assessor metadata validation
    response digest
    derived semantic-judge facts

T11.3c
    exact human-review artifact and response binding

T11.4
    pure APPROVE/REJECT materialization
```

T11.3a0 is a behavior-preserving infrastructure refactor, not a semantic change. Existing historical callers must retain their observable serialization/hash/import behavior and exception compatibility. Domains with intentionally different identity rules are not mechanically collapsed into the shared primitive merely because they also use JSON/SHA-256.

### Deferred T12 closure

D59 does not resolve final T12 top-level `model_id` / `model_version` / `authority_kind` selection for every producer outcome. D57 already separates semantic-model provenance, human-review provenance, deterministic gates, and policy authority. Any remaining exact T12 mapping must be frozen before T12 producer implementation and must not be silently decided inside T11.

### Reason

The semantic verdict and historical provenance answer different questions.

D59 makes each T11 semantic step independently inspectable and cryptographically bound:

```text
exact request content
    -> request_digest
exact semantic proposal
    -> response_digest
exact human decision
    -> response_digest binding
```

The semantic model cannot claim novelty, lifecycle identity, session identity, or EventLog authority. The reviewer cannot silently rewrite the proposal. Changing request text or learner evidence changes request identity; changing semantic output changes response identity; changing assessor metadata changes provenance without falsely creating a different semantic proposal.

This preserves D51–D58 while giving T11.3 deterministic, fail-closed artifact boundaries that can be implemented and tested without Anki, EventLog, exposure history, or lifecycle state.

### Out of scope

D59 does not define or implement:

- paid OpenAI, Anthropic, Azure, or other LLM/speech APIs;
- browser or ChatGPT UI automation;
- session scheduling;
- session manifests;
- attempt/cognitive-stimulus/response-artifact construction;
- exposure reservation or novelty;
- EventLog emission;
- T12 producer-history preflight/recovery;
- T9 observation or lifecycle transitions;
- Anki reads/writes;
- local STT execution or transcript-verification workflow;
- semantic confidence thresholds;
- automatic supersession/rejudgment history;
- precedence among multiple simultaneous W/S lexical-error categories;
- pronunciation, accent, prosody, or generic language-proficiency scoring;
- globally immutable protocol/rubric/prompt text registries.

## D60 — Durable response capture receipts and immutable artifact-store boundary

**Date:** 2026-08-25
**Status:** Accepted

T12 owns a second append-only ledger at an explicit runtime path:

    t12-captures.jsonl

The D55 exposure ledger remains unchanged and retains exactly one authority:

    which attempt/stimulus exposures consumed novelty

The capture ledger has exactly one authority:

    which immutable learner-response artifact was durably captured
    for which attempt

EventLog remains authoritative for final outcomes.

The immutable artifact store is authoritative only for exact bytes.

No store duplicates another store's fact.

Capture record v1 contains exactly:

    v
    producer
    producer_version
    captured_at
    attempt_id
    response_artifact_ref

Rules:

    v == 1

    producer == "t12-assessment"

    producer_version == 1

    captured_at is normalized UTC ISO-8601 with explicit +00:00

    captured_at is audit metadata only

    attempt_id must satisfy the frozen attempt-id grammar

    response_artifact_ref must satisfy the frozen sha256 artifact-ref grammar

Capture-ledger slot identity is exactly:

    producer
    producer_version
    attempt_id

Duplicate physical capture slots fail closed even if identical.

A valid capture requires:

    exactly one compatible durable D55 exposure reservation
    for the same attempt_id

AND

    the referenced immutable artifact exists

AND

    its exact bytes hash back to response_artifact_ref

Capture sequence is exactly:

    1. validate exposure history
    2. validate capture history
    3. establish exactly one matching reservation
    4. durably persist immutable response bytes
    5. flush/fsync artifact
    6. exact artifact readback/hash verification
    7. append capture receipt
    8. flush/fsync
    9. exact capture-record readback

A response is resumable after restart IFF:

    one valid capture receipt exists for the attempt
    AND the referenced artifact verifies

Artifact existence without a capture receipt NEVER establishes capture.

Such an artifact is an inert orphan and may remain on disk.

Capture receipt without a valid referenced artifact is corruption.

Two different attempts MAY reference the same response_artifact_ref
when learner response bytes are identical.

Their attempt bindings remain independent because capture receipts are
attempt-scoped.

Before the first exposure reservation, exposure and capture ledgers are
initialized together.

If exposure history is non-empty while the capture-ledger file is unexpectedly
missing, fail closed.

If capture history exists while the exposure ledger is missing, fail closed.

The immutable artifact store:

    stores exact bytes by sha256 content identity
    contains no attempt/session/unit/channel/outcome/novelty index
    performs no directory-listing-based recovery
    never acts as attempt→response authority

## D61 — T12 final outcome authority mapping and producer operational closure

**Date:** 2026-08-25
**Status:** Accepted

Top-level authority means:

    the authority responsible for the final stored outcome

Provenance means:

    every stage actually invoked to establish that outcome

For T12 producer v1 the mapping is frozen:

Semantic PASS/FAIL, after exact human APPROVE:
    authority_kind = "semantic_model"

    model_id      = exact imported T11 assessor_id
    model_version = exact imported T11 assessor_version

This applies to:
    R PASS/FAIL
    L PASS/FAIL
    W PASS/FAIL after target-present gate
    S PASS/FAIL after verified transcription and target-present gate

OMITTED:
    authority_kind = "deterministic_gate"

    model_id      = "d19-target-presence"
    model_version = "1"

Presence-gate provenance is:

    gate_id      = "d19-target-presence"
    gate_version = 1
    target_present = false

ALL ABSTAIN outcomes:
    authority_kind = "policy"

    model_id      = "t12-assessment-policy"
    model_version = "1"

Policy provenance is:

    policy_id      = "t12-assessment-policy"
    policy_version = 1

This includes:
    off_topic
    refusal
    explicit_skip
    no_response
    insufficient_lexical_evidence
    response_unintelligible
    audio_unusable
    transcription_uncertain
    transcription_failed
    semantic_uncertainty
    reviewer_rejected
    invalid_artifact
    infrastructure_failure

The provenance object must still preserve every stage actually invoked.

Examples:

semantic uncertainty:
    successfully invoked prerequisites
    semantic_judge
    policy

reviewer rejected:
    successfully invoked prerequisites
    semantic_judge
    human_review
    policy

transcription uncertainty:
    transcription
    policy

The HUMAN_REVIEW authority_kind enum member remains legal at the generic
contract level but is not selected as the top-level authority by the current
T12 v1 planner.

For SPEAK, top-level model_id/model_version/authority_kind use the same final
outcome authority mapping as its companion JUDGE.

audio_path is advisory/audit metadata only.

response_audio_ref is the authoritative raw speech-response identity.

Moving a file path must not create a new response or assessment identity.

T12 complete EventLog preflight must use the existing EventLog decoder but must
treat EventLogCorruptionWarning as an exception. A malformed final event record
must therefore fail T12 preflight rather than be silently ignored.

Do not create a second EventLog parser.

Generic EventLog.log() remains generic for historical compatibility.

Repository production code must not emit a new D35-bearing JUDGE except through
the future T12 assessment producer. This will be guarded by an AST/static
invariant test rather than by adding T12-specific authorization state to
EventLog.
