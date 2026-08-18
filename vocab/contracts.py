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
_SLUG_PATTERN: Final[str] = r"[a-z0-9]+(?:-[a-z0-9]+)*"
UNIT_KEY_PATTERN: Final[str] = (
    rf"^{_SLUG_PATTERN}{re.escape(UNIT_KEY_SEPARATOR)}{_SLUG_PATTERN}$"
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

DEFAULT_TARGET_FIELD: Final[str] = "Target_R"

TARGET_FIELDS_REQUIRING_JUSTIFICATION: Final[tuple[str, ...]] = (
    "Target_W",
    "Target_S",
)

CONTEXT_FIELDS: Final[tuple[str, ...]] = (
    "Ctx_1",
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

EVENT_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "v",
    "ts",
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

# ts must be ISO 8601 with an explicit UTC offset, never naive local time.
EVENT_TIMESTAMP_REQUIRES_OFFSET: Final[bool] = True


# ============================================================
# 6. STATE MACHINE CONTRACT
# ============================================================

STATE_NEW: Final[str] = "NEW"
STATE_LEARNING: Final[str] = "LEARNING"
STATE_STABLE: Final[str] = "STABLE"
STATE_MASTERED: Final[str] = "MASTERED"
STATE_DORMANT: Final[str] = "DORMANT"
STATE_RELAPSE: Final[str] = "RELAPSE"

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
STABLE_MIN_AGE_DAYS: Final[int] = 30

MASTERED_MIN_SESSION_PASSES: Final[int] = 2
MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS: Final[int] = 7

MASTERED_TO_DORMANT_DAYS: Final[int] = 30
LEECH_LAPSE_THRESHOLD: Final[int] = 4
PARAMETER_RECALIBRATION_AFTER_DAYS: Final[int] = 90

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

# Leech is intentionally not a wildcard transition. LEARNING->LEARNING is a
# no-op and must not emit repeated STATE events.
LEECH_SOURCE_STATES: Final[tuple[str, ...]] = (
    STATE_STABLE,
    STATE_MASTERED,
)
LEECH_TARGET_STATE: Final[str] = STATE_LEARNING
EMIT_STATE_EVENT_ON_NOOP: Final[bool] = False

# Reactivation is channel-specific. UnitProgress therefore carries
# failed_channels explicitly.
RELAPSE_REACTIVATE_FAILED_CHANNEL_ONLY: Final[bool] = True

# Unit-level dormancy is only valid once every enabled channel has reached
# MASTERED (and the dormancy timing rule is satisfied by reconcile.py).
DORMANT_REQUIRES_ALL_ACTIVE_CHANNELS_MASTERED: Final[bool] = True


# ============================================================
# 7. RECONCILIATION / RETENTION CONTRACT
# ============================================================

DORMANT_CLEAR_FIELDS: Final[tuple[str, ...]] = MEDIA_FIELDS
DORMANT_DELETE_NOTE: Final[bool] = False
DORMANT_PRESERVE_REVLOG: Final[bool] = True


# ============================================================
# 8. ANKI CONTRACT
# ============================================================

ANKI_LEECH_THRESHOLD: Final[int] = 4
ANKI_LEECH_ACTION: Final[str] = "tag_only"
ANKI_LEECH_TAG: Final[str] = "leech"
