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
