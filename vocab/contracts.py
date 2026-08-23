"""
Frozen contracts for the vocabulary learning system.

This module defines constants and structural invariants only.
It must not perform I/O, call external services, repair data, or invent
missing values.

Validation belongs in validators.py.
State-transition execution belongs in reconcile.py.

Engineering thresholds are parameters, not truths. Recalibrate them after
90 days of real usage.
"""

from __future__ import annotations

import re
from typing import Final


# ============================================================
# 1. UNIT KEY CONTRACT
# ============================================================

UNIT_KEY_SEPARATOR: Final[str] = "::"

# <lemma-slug>::<sense-slug>
#
# Rules:
# - lowercase
# - ASCII
# - alphanumeric segments separated by "-"
# - lemma_slug and sense_slug are human-approved once at creation time;
#   no automatic slug regeneration is allowed later.
SLUG_PATTERN: Final[str] = r"[a-z0-9]+(?:-[a-z0-9]+)*"
UNIT_KEY_PATTERN: Final[str] = (
    rf"^{SLUG_PATTERN}{re.escape(UNIT_KEY_SEPARATOR)}{SLUG_PATTERN}$"
)


# ============================================================
# 2. NOTE TYPE CONTRACT
# ============================================================

# The persisted note schema. The aggregate state is deliberately NOT stored:
# it is derived from state_R/state_L/state_W/state_S so there is only one
# source of truth for lifecycle state.
NOTE_FIELDS: Final[tuple[str, ...]] = (
    "unit_key",
    "lemma",
    "lemma_slug",
    "sense_slug",
    "unit_type",
    "Target_R",
    "Target_L",
    "Target_W",
    "Target_S",
    "register",
    "definition_en",
    "source_ref",
    "source_sentence",
    "Ctx_1",
    "Ctx_2",
    "Ctx_3",
    "Ctx_4",
    "Ctx_5",
    "audio_1",
    "audio_2",
    "audio_3",
    "VisualCue",
    "state_R",
    "state_L",
    "state_W",
    "state_S",
    "freq_band",
    "created",
    "graduated",
)

UNIQUE_NOTE_FIELD: Final[str] = "unit_key"

# Stable note identity is assigned once at creation time.
# Changing any of these fields would change the identity of the Unit rather
# than update its mutable learning state.
IMMUTABLE_NOTE_FIELDS: Final[tuple[str, ...]] = (
    "unit_key",
    "lemma_slug",
    "sense_slug",
)

CHANNELS: Final[tuple[str, ...]] = ("R", "L", "W", "S")

TARGET_FIELDS: Final[tuple[str, ...]] = (
    "Target_R",
    "Target_L",
    "Target_W",
    "Target_S",
)

STATE_FIELDS: Final[tuple[str, ...]] = (
    "state_R",
    "state_L",
    "state_W",
    "state_S",
)

TARGET_FIELD_BY_CHANNEL: Final[dict[str, str]] = {
    "R": "Target_R",
    "L": "Target_L",
    "W": "Target_W",
    "S": "Target_S",
}

STATE_FIELD_BY_CHANNEL: Final[dict[str, str]] = {
    "R": "state_R",
    "L": "state_L",
    "W": "state_W",
    "S": "state_S",
}

# Card templates are identified by stable template names, never by ordinal.
CARD_TEMPLATE_NAMES: Final[tuple[str, ...]] = ("R", "L", "W", "S")
CHANNEL_BY_TEMPLATE_NAME: Final[dict[str, str]] = {
    "R": "R",
    "L": "L",
    "W": "W",
    "S": "S",
}

DEFAULT_TARGET_FIELD: Final[str] = "Target_R"
TARGET_FLAG_VALUE: Final[str] = "1"
TARGET_FLAG_VALUES: Final[tuple[str, ...]] = (
    "",
    TARGET_FLAG_VALUE,
)
# Productive-channel targeting requires explicit provenance from FORGE.
# The justification is NOT stored in the Anki note. It belongs in the
# FORGE event payload so the note keeps current state while the event log
# preserves why W/S was enabled.
TARGET_CHANNELS_REQUIRING_JUSTIFICATION: Final[tuple[str, ...]] = (
    "W",
    "S",
)

FORGE_TARGET_JUSTIFICATION_PAYLOAD_FIELD: Final[str] = (
    "target_justification"
)

CONTEXT_FIELDS: Final[tuple[str, ...]] = (
    "Ctx_1",
    "Ctx_2",
    "Ctx_3",
    "Ctx_4",
    "Ctx_5",
)

ANKI_REVIEW_CONTEXT_FIELD: Final[str] = "Ctx_1"

NOVEL_CONTEXT_FIELDS: Final[tuple[str, ...]] = (
    "Ctx_2",
    "Ctx_3",
    "Ctx_4",
    "Ctx_5",
)

AUDIO_FIELDS: Final[tuple[str, ...]] = (
    "audio_1",
    "audio_2",
    "audio_3",
)

MEDIA_FIELDS: Final[tuple[str, ...]] = (
    *AUDIO_FIELDS,
    "VisualCue",
)


# ============================================================
# 3. FORGE / CONTEXT CONTRACT
# ============================================================
UNIT_TYPE_VALUES: Final[tuple[str, ...]] = (
    "word",
    "chunk",
    "frame",
)

# Shared deterministic Unit-matching contract for T5 validation and
# T10 corpus scanning.

TEXT_NORMALIZATION_FORM: Final[str] = "NFKC"

# Normalize these common Unicode apostrophe forms to ASCII apostrophe
# before tokenization.
APOSTROPHE_EQUIVALENTS: Final[tuple[str, ...]] = (
    "\u2018",  # LEFT SINGLE QUOTATION MARK
    "\u2019",  # RIGHT SINGLE QUOTATION MARK
    "\u02bc",  # MODIFIER LETTER APOSTROPHE
    "\uff07",  # FULLWIDTH APOSTROPHE
)

CANONICAL_APOSTROPHE: Final[str] = "'"

# One lexical token:
# - Unicode letters/digits are allowed;
# - underscore is not lexical content;
# - apostrophe may occur only inside the token.
#
# Examples:
# don't -> one token
# state-of-the-art -> four tokens
LEXICAL_TOKEN_PATTERN: Final[str] = (
    r"[^\W_]+(?:'[^\W_]+)*"
)

CHUNK_MAX_INSERTED_TOKENS: Final[int] = 2

FRAME_PLACEHOLDER: Final[str] = "___"
FRAME_PLACEHOLDER_COUNT: Final[int] = 1
FRAME_MIN_FIXED_TOKENS: Final[int] = 2
FRAME_SLOT_MIN_TOKENS: Final[int] = 1
FRAME_SLOT_MAX_TOKENS: Final[int] = 6

SOURCE_REF_KINDS: Final[tuple[str, ...]] = (
    "dictionary",
    "corpus",
)

# Internal evidence reference:
# <kind>:<namespace>:<resource-id>
#
# Examples:
# dictionary:cambridge:subtle
# corpus:ragtrust-papers:2405-12345
#
# This contract validates reference syntax only. Validators must not resolve
# the reference or require the underlying resource to exist.
_SOURCE_REF_KIND_PATTERN: Final[str] = "|".join(
    re.escape(kind) for kind in SOURCE_REF_KINDS
)

_SOURCE_REF_NAMESPACE_PATTERN: Final[str] = (
    r"[a-z0-9]+(?:-[a-z0-9]+)*"
)

_SOURCE_REF_RESOURCE_PATTERN: Final[str] = (
    r"[a-z0-9][a-z0-9._-]*"
)

SOURCE_REF_PATTERN: Final[str] = (
    rf"^(?:{_SOURCE_REF_KIND_PATTERN}):"
    rf"{_SOURCE_REF_NAMESPACE_PATTERN}:"
    rf"{_SOURCE_REF_RESOURCE_PATTERN}$"
)

CONTEXT_VIOLATION_CODES: Final[tuple[str, ...]] = (
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

FORGE_VIOLATION_CODES: Final[tuple[str, ...]] = (
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

DEFINITION_FIELD: Final[str] = "definition_en"
SOURCE_REFERENCE_FIELD: Final[str] = "source_ref"
SOURCE_SENTENCE_FIELD: Final[str] = "source_sentence"

FORGE_CONTEXT_COUNT: Final[int] = 5

# Contexts are intended to contain the target Unit. The deterministic guard is
# against copying the source sentence too closely, not against containing the
# lemma itself.
CTX_MUST_CONTAIN_LEMMA: Final[bool] = True
CTX_MUST_BE_PAIRWISE_DISTINCT: Final[bool] = True
CTX_MAX_SOURCE_TOKEN_OVERLAP: Final[float] = 0.60
CTX_OVERLAP_EXCLUDES_UNIT_TOKENS: Final[bool] = True
CTX_MIN_TOKENS: Final[int] = 8
CTX_MIN_RESIDUAL_TOKENS: Final[int] = 6

# "Different topic" remains a FORGE prompt requirement; it is intentionally
# not claimed as a deterministic validator invariant.


# ============================================================
# 4. REGISTER CONTRACT
# ============================================================

REGISTER_VALUES: Final[tuple[str, ...]] = (
    "academic",
    "neutral",
    "conversational",
    "technical",
)


# ============================================================
# 5. EVENT CONTRACT
# ============================================================

EVENT_SCHEMA_VERSION: Final[int] = 1

EVENT_TYPES: Final[tuple[str, ...]] = (
    "REVIEW",
    "JUDGE",
    "FORGE",
    "STATE",
    "SPEAK",
    "ENCOUNTER",
)

# Known event types that remain readable/recognized but must not be emitted
# through EventLog.log() in v0.
RESERVED_EVENT_TYPES: Final[tuple[str, ...]] = (
    "REVIEW",
)

EVENT_PAYLOAD_REQUIRED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "REVIEW": (),
    "JUDGE": (
        "channel",
        "passed",
        "model_id",
        "model_version",
    ),
    "FORGE": (
        "source_ref",
        "accepted",
    ),
    "STATE": (
        "channel",
        "from",
        "to",
        "trigger",
    ),
    "SPEAK": (
        "audio_path",
        "transcript",
        "passed",
        "model_id",
        "model_version",
    ),
    "ENCOUNTER": (
        "count",
        "source",
        "month",
    ),
}

# Additional producer-level requirements for JUDGE evidence that may gate a
# T9 lifecycle transition. These are deliberately not unconditional v1 event
# decoder requirements, so historical/non-lifecycle JUDGE records remain
# readable.
LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "assessment_id",
    "stimulus_ref",
    "novel",
)

EVENT_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "v",
    "ts",
    "day",
    "event",
    "unit_key",
    "payload",
)

EVENTS_REQUIRING_MODEL_METADATA: Final[tuple[str, ...]] = (
    "JUDGE",
    "SPEAK",
)

MODEL_METADATA_FIELDS: Final[tuple[str, ...]] = (
    "model_id",
    "model_version",
)

# Event time has two separate meanings:
# - ts: one globally comparable instant, always normalized to UTC (+00:00)
# - day: the local calendar day used for daily reports
EVENT_TIMESTAMP_UTC_OFFSET: Final[str] = "+00:00"
EVENT_LOCAL_TIMEZONE: Final[str] = "Asia/Ho_Chi_Minh"
EVENT_DAY_FORMAT: Final[str] = "%Y-%m-%d"

# STATE events are channel-scoped; aggregate state is never persisted.
STATE_EVENT_REQUIRED_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    EVENT_PAYLOAD_REQUIRED_FIELDS["STATE"]
)

# T9 producer requirements extend the backwards-compatible generic STATE
# payload without changing the EVENT_SCHEMA_VERSION=1 decoder contract.
T9_STATE_REQUIRED_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "channel",
    "from",
    "to",
    "trigger",
    "transition_id",
    "from_episode_id",
    "phase",
    "evidence",
)

T9_STATE_OPTIONAL_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "transition_group_id",
)

INITIAL_NEW_EPISODE_PREFIX: Final[str] = "initial-new:"
T9_DORMANCY_GROUP_KIND: Final[str] = "DORMANCY"

T9_STATE_PHASE_PREPARE: Final[str] = "PREPARE"
T9_STATE_PHASE_COMMIT: Final[str] = "COMMIT"
T9_STATE_PHASE_ABORT: Final[str] = "ABORT"

T9_STATE_PHASES: Final[tuple[str, ...]] = (
    T9_STATE_PHASE_PREPARE,
    T9_STATE_PHASE_COMMIT,
    T9_STATE_PHASE_ABORT,
)


# ============================================================
# 5A. T10 CORPUS / ENCOUNTER PRODUCER CONTRACT
# ============================================================

CORPUS_SCAN_VERSION: Final[int] = 1
CORPUS_MONTH_PATTERN: Final[str] = r"^\d{4}-(?:0[1-9]|1[0-2])$"
CORPUS_SOURCE_PATTERN: Final[str] = rf"^{SLUG_PATTERN}$"

CORPUS_EXTENSIONS: Final[tuple[str, ...]] = (
    ".txt",
)
CORPUS_DIRECT_CHILDREN_ONLY: Final[bool] = True
CORPUS_ALLOW_SYMLINKS: Final[bool] = False
CORPUS_ALLOW_UTF8_BOM: Final[bool] = True
CORPUS_RAW_BYTES_DEFINE_FILE_IDENTITY: Final[bool] = True

CORPUS_SENTENCE_TERMINATORS: Final[tuple[str, ...]] = (
    ".",
    "!",
    "?",
    "…",
)
CORPUS_BLANK_LINE_IS_BLOCK_BOUNDARY: Final[bool] = True
CORPUS_SINGLE_NEWLINE_IS_BLOCK_BOUNDARY: Final[bool] = False

CORPUS_REJECT_URL_PREFIXES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "www.",
)

T10_ENCOUNTER_PRODUCER_ID: Final[str] = "t10-corpus"
T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "count",
    "source",
    "month",
    "producer",
    "scan_version",
    "encounter_id",
    "lemma",
    "unit_type",
    "corpus_snapshot_digest",
    "corpus_file_count",
)
T10_ENCOUNTER_EMIT_ZERO_COUNTS: Final[bool] = True


STATE_TRIGGER_FIRST_REVIEW: Final[str] = "FIRST_REVIEW"
STATE_TRIGGER_STABILITY_GATE: Final[str] = "STABILITY_GATE"
STATE_TRIGGER_REVIEW_LAPSE: Final[str] = "REVIEW_LAPSE"
STATE_TRIGGER_MASTERY_ASSESSMENT_PASS: Final[str] = (
    "MASTERY_ASSESSMENT_PASS"
)
STATE_TRIGGER_ASSESSMENT_FAIL: Final[str] = "ASSESSMENT_FAIL"
STATE_TRIGGER_DORMANCY_ELAPSED: Final[str] = "DORMANCY_ELAPSED"
STATE_TRIGGER_RELAPSE_REVIEW: Final[str] = "RELAPSE_REVIEW"

STATE_TRIGGERS: Final[tuple[str, ...]] = (
    STATE_TRIGGER_FIRST_REVIEW,
    STATE_TRIGGER_STABILITY_GATE,
    STATE_TRIGGER_REVIEW_LAPSE,
    STATE_TRIGGER_MASTERY_ASSESSMENT_PASS,
    STATE_TRIGGER_ASSESSMENT_FAIL,
    STATE_TRIGGER_DORMANCY_ELAPSED,
    STATE_TRIGGER_RELAPSE_REVIEW,
)

# ============================================================
# 6. STATE MACHINE CONTRACT
# ============================================================

STATE_NEW: Final[str] = "NEW"
STATE_LEARNING: Final[str] = "LEARNING"
STATE_STABLE: Final[str] = "STABLE"
STATE_MASTERED: Final[str] = "MASTERED"
STATE_DORMANT: Final[str] = "DORMANT"
STATE_RELAPSE: Final[str] = "RELAPSE"

# Diagnostic sentinel returned by derived_state() for corrupted active-channel
# data. UNKNOWN is deliberately NOT a lifecycle state and has no transitions.
STATE_UNKNOWN: Final[str] = "UNKNOWN"

STATES: Final[tuple[str, ...]] = (
    STATE_NEW,
    STATE_LEARNING,
    STATE_STABLE,
    STATE_MASTERED,
    STATE_DORMANT,
    STATE_RELAPSE,
)

# Used only to derive an aggregate display state from active channel states.
# RELAPSE has highest urgency, then NEW/LEARNING/STABLE/MASTERED/DORMANT.
DERIVED_STATE_PRIORITY: Final[tuple[str, ...]] = (
    STATE_RELAPSE,
    STATE_NEW,
    STATE_LEARNING,
    STATE_STABLE,
    STATE_MASTERED,
    STATE_DORMANT,
)

# Engineering parameters.
STABLE_MIN_INTERVAL_DAYS: Final[int] = 21
STABLE_ZERO_LAPSE_WINDOW_DAYS: Final[int] = 30
STABLE_MIN_AGE_DAYS: Final[int] = STABLE_MIN_INTERVAL_DAYS

MASTERED_MIN_SESSION_PASSES: Final[int] = 2
MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS: Final[int] = 7

MASTERED_TO_DORMANT_DAYS: Final[int] = 30
LEECH_LAPSE_THRESHOLD: Final[int] = 4
PARAMETER_RECALIBRATION_AFTER_DAYS: Final[int] = 90

LIFECYCLE_SECONDS_PER_DAY: Final[int] = 86400

TRANSITION_NEW_TO_LEARNING: Final[tuple[str, str]] = (
    STATE_NEW,
    STATE_LEARNING,
)
TRANSITION_LEARNING_TO_STABLE: Final[tuple[str, str]] = (
    STATE_LEARNING,
    STATE_STABLE,
)
TRANSITION_STABLE_TO_MASTERED: Final[tuple[str, str]] = (
    STATE_STABLE,
    STATE_MASTERED,
)
TRANSITION_MASTERED_TO_DORMANT: Final[tuple[str, str]] = (
    STATE_MASTERED,
    STATE_DORMANT,
)
TRANSITION_DORMANT_TO_RELAPSE: Final[tuple[str, str]] = (
    STATE_DORMANT,
    STATE_RELAPSE,
)
TRANSITION_RELAPSE_TO_LEARNING: Final[tuple[str, str]] = (
    STATE_RELAPSE,
    STATE_LEARNING,
)

# Backward transitions required to represent degradation before dormancy.
TRANSITION_STABLE_TO_LEARNING: Final[tuple[str, str]] = (
    STATE_STABLE,
    STATE_LEARNING,
)
TRANSITION_MASTERED_TO_RELAPSE: Final[tuple[str, str]] = (
    STATE_MASTERED,
    STATE_RELAPSE,
)

STATE_TRANSITIONS: Final[tuple[tuple[str, str], ...]] = (
    TRANSITION_NEW_TO_LEARNING,
    TRANSITION_LEARNING_TO_STABLE,
    TRANSITION_STABLE_TO_MASTERED,
    TRANSITION_MASTERED_TO_DORMANT,
    TRANSITION_DORMANT_TO_RELAPSE,
    TRANSITION_RELAPSE_TO_LEARNING,
    TRANSITION_STABLE_TO_LEARNING,
    TRANSITION_MASTERED_TO_RELAPSE,
)

# Leech is a note-level rescue signal from Anki, not a lifecycle transition.
# Degradation is represented by explicit per-channel state transitions above.
RELAPSE_REACTIVATE_FAILED_CHANNEL_ONLY: Final[bool] = True

# T9 never silently removes suspension. Reactivation is a separate,
# human-confirmed action restricted to the failed channel's card.
T9_AUTO_UNSUSPEND: Final[bool] = False
T9_UNSUSPEND_REQUIRES_HUMAN_CONFIRMATION: Final[bool] = True

# Leech evidence is diagnostic only in T9 v0.
T9_LEECH_AUTO_TRANSITION: Final[bool] = False
T9_LEECH_AUTOCREATE_VISUAL_CUE: Final[bool] = False

# Unit-level dormancy is only valid once every enabled channel has reached
# MASTERED (and the dormancy timing rule is satisfied by reconcile.py).
DORMANT_REQUIRES_ALL_ACTIVE_CHANNELS_MASTERED: Final[bool] = True


# ============================================================
# 7. RECONCILIATION / RETENTION CONTRACT
# ============================================================

DORMANT_CLEAR_FIELDS: Final[tuple[str, ...]] = ()
DORMANT_DELETE_NOTE: Final[bool] = False
DORMANT_PRESERVE_REVLOG: Final[bool] = True


# ============================================================
# 8. ANKI CONTRACT
# ============================================================
ANKI_NOTE_TYPE_NAME: Final[str] = "VocabularyUnit"
ANKI_SORT_FIELD: Final[str] = "unit_key"

ANKI_LEECH_THRESHOLD: Final[int] = LEECH_LAPSE_THRESHOLD
ANKI_LEECH_ACTION: Final[str] = "tag_only"
ANKI_LEECH_TAG: Final[str] = "leech"

# Anki revlog type/ease semantics consumed by T9 observation. Cram entries are
# recognized but are not lifecycle evidence.
REVLOG_TYPE_LEARNING: Final[int] = 0
REVLOG_TYPE_REVIEW: Final[int] = 1
REVLOG_TYPE_RELEARNING: Final[int] = 2
REVLOG_TYPE_CRAM: Final[int] = 3

REVLOG_LIFECYCLE_TYPES: Final[tuple[int, ...]] = (
    REVLOG_TYPE_LEARNING,
    REVLOG_TYPE_REVIEW,
    REVLOG_TYPE_RELEARNING,
)

REVLOG_EASE_AGAIN: Final[int] = 1

# Anki cardsInfo queue sentinel. Buried queues are not suspension.
ANKI_QUEUE_SUSPENDED: Final[int] = -1
