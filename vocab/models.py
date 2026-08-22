"""
Pure data models for the vocabulary learning system.

This module contains data structures and pure transformations only.
It must not perform file I/O, call Anki, call LLM/Azure services, validate,
repair, slugify, or invent missing data.

Validation belongs in validators.py.
Persistence belongs in modules such as events.py and anki.py.
State-transition decisions belong in reconcile.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    DERIVED_STATE_PRIORITY,
    STATE_UNKNOWN,
    STATES,
    UNIT_KEY_SEPARATOR,
)


# ============================================================
# 1. VALIDATION RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class Violation:
    """One deterministic contract violation."""

    field_name: str
    code: str
    message: str


# ============================================================
# 2. VOCABULARY UNIT
# ============================================================


@dataclass(slots=True)
class VocabUnit:
    """
    One vocabulary Unit corresponding to one Anki note.

    This model may temporarily contain invalid or incomplete values; it does
    not validate itself. validators.py owns acceptance/rejection.

    Empty Target_* means that channel is disabled and Anki should not create
    that card. The corresponding state_* field should also be empty.

    to_note_fields() is a FULL-REPLACEMENT serialization intended for note
    creation or for a fully hydrated note. Do not use it for partial updates.
    anki.update_note_fields() must accept an explicit subset mapping so an
    incomplete model cannot silently erase Ctx_* or media fields.
    """

    # Identity / lexical key
    unit_key: str
    lemma: str
    lemma_slug: str
    sense_slug: str
    unit_type: str

    # Target channels
    Target_R: str = ""
    Target_L: str = ""
    Target_W: str = ""
    Target_S: str = ""

    # Core lexical information
    register: str = ""
    definition_en: str = ""
    source_ref: str = ""
    source_sentence: str = ""

    # Context bank
    Ctx_1: str = ""
    Ctx_2: str = ""
    Ctx_3: str = ""
    Ctx_4: str = ""
    Ctx_5: str = ""

    # Media
    audio_1: str = ""
    audio_2: str = ""
    audio_3: str = ""
    VisualCue: str = ""

    # Per-channel lifecycle state. A channel is disabled when its Target_* and
    # state_* are both empty. FORGE enables a channel by setting both together.
    state_R: str = ""
    state_L: str = ""
    state_W: str = ""
    state_S: str = ""

    # Lifecycle metadata
    freq_band: str = ""
    created: str = ""
    graduated: str = ""

    def target_fields(self) -> dict[str, str]:
        """Return target-channel values without mutating the Unit."""
        return {
            "Target_R": self.Target_R,
            "Target_L": self.Target_L,
            "Target_W": self.Target_W,
            "Target_S": self.Target_S,
        }

    def state_fields(self) -> dict[str, str]:
        """Return per-channel lifecycle states."""
        return {
            "state_R": self.state_R,
            "state_L": self.state_L,
            "state_W": self.state_W,
            "state_S": self.state_S,
        }

    def context_fields(self) -> dict[str, str]:
        """Return the five context fields."""
        return {
            "Ctx_1": self.Ctx_1,
            "Ctx_2": self.Ctx_2,
            "Ctx_3": self.Ctx_3,
            "Ctx_4": self.Ctx_4,
            "Ctx_5": self.Ctx_5,
        }

    def audio_fields(self) -> dict[str, str]:
        """Return the three audio fields."""
        return {
            "audio_1": self.audio_1,
            "audio_2": self.audio_2,
            "audio_3": self.audio_3,
        }

    def active_channel_states(self) -> dict[str, str]:
        """
        Return states only for enabled target channels.

        This is a pure projection, not validation. If a target is enabled but
        its state is empty, the empty state is returned and validators.py must
        reject the Unit.
        """
        targets = {
            "R": self.Target_R,
            "L": self.Target_L,
            "W": self.Target_W,
            "S": self.Target_S,
        }
        states = {
            "R": self.state_R,
            "L": self.state_L,
            "W": self.state_W,
            "S": self.state_S,
        }
        return {
            channel: states[channel]
            for channel, target in targets.items()
            if target != ""
        }

    def derived_state(self) -> str:
        """
        Derive one aggregate display state from enabled channels.

        The aggregate is NOT persisted and is NOT a source of truth.
        RELAPSE has highest urgency; otherwise the weakest progress state in
        DERIVED_STATE_PRIORITY wins.

        Returns an empty string when no channel is enabled.
        """
        active_states = tuple(self.active_channel_states().values())
        if not active_states:
            return ""

        if any(state not in STATES for state in active_states):
            return STATE_UNKNOWN

        for candidate in DERIVED_STATE_PRIORITY:
            if candidate in active_states:
                return candidate

        # Defensive fail-closed fallback. With valid contracts this branch is
        # unreachable because DERIVED_STATE_PRIORITY covers every lifecycle state.
        return STATE_UNKNOWN

    def to_note_fields(self) -> dict[str, str]:
        """
        Serialize the complete Anki note field mapping.

        FULL REPLACEMENT ONLY. Never use this mapping for a partial update.
        """
        return {
            "unit_key": self.unit_key,
            "lemma": self.lemma,
            "lemma_slug": self.lemma_slug,
            "sense_slug": self.sense_slug,
            "unit_type": self.unit_type,
            "Target_R": self.Target_R,
            "Target_L": self.Target_L,
            "Target_W": self.Target_W,
            "Target_S": self.Target_S,
            "register": self.register,
            "definition_en": self.definition_en,
            "source_ref": self.source_ref,
            "source_sentence": self.source_sentence,
            "Ctx_1": self.Ctx_1,
            "Ctx_2": self.Ctx_2,
            "Ctx_3": self.Ctx_3,
            "Ctx_4": self.Ctx_4,
            "Ctx_5": self.Ctx_5,
            "audio_1": self.audio_1,
            "audio_2": self.audio_2,
            "audio_3": self.audio_3,
            "VisualCue": self.VisualCue,
            "state_R": self.state_R,
            "state_L": self.state_L,
            "state_W": self.state_W,
            "state_S": self.state_S,
            "freq_band": self.freq_band,
            "created": self.created,
            "graduated": self.graduated,
        }


# ============================================================
# 3. EVENT
# ============================================================


@dataclass(frozen=True, slots=True)
class Event:
    """One append-only event. Event validity is checked elsewhere."""

    v: int
    ts: str
    day: str
    event: str
    unit_key: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "ts": self.ts,
            "day": self.day,
            "event": self.event,
            "unit_key": self.unit_key,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """Identity attached to JUDGE/SPEAK events, including human judges."""

    model_id: str
    model_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
        }


# ============================================================
# 4. FORGE DATA
# ============================================================


@dataclass(slots=True)
class ForgeCandidate:
    """
    Candidate entering FORGE before deterministic validation.

    lemma_slug and sense_slug must already be human-approved. This class does
    not slugify or regenerate either value.
    """

    lemma: str
    lemma_slug: str
    sense_slug: str
    source_ref: str
    source_sentence: str

    unit_type: str = ""
    definition_en: str = ""
    register: str = ""

    def proposed_unit_key(self) -> str:
        """Compose a key from already-approved slugs; never slugify here."""
        return f"{self.lemma_slug}{UNIT_KEY_SEPARATOR}{self.sense_slug}"


@dataclass(frozen=True, slots=True)
class ForgeRejection:
    """A Unit rejected by deterministic validation."""

    unit_key: str
    violations: tuple[Violation, ...]
    raw_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_key": self.unit_key,
            "violations": [
                {
                    "field_name": violation.field_name,
                    "code": violation.code,
                    "message": violation.message,
                }
                for violation in self.violations
            ],
            "raw_output": dict(self.raw_output),
        }


# ============================================================
# 5. STATE / RECONCILIATION OBSERVATIONS
# ============================================================


@dataclass(frozen=True, slots=True)
class LifecycleAssessment:
    """One channel-scoped JUDGE observation eligible for T9 evaluation."""

    channel: str
    passed: bool
    assessment_id: str
    stimulus_ref: str
    novel: bool
    ts: str
    model_id: str
    model_version: str


@dataclass(frozen=True, slots=True)
class ChannelProgress:
    """
    Observed review/assessment state for one enabled target channel.

    Data that gates a per-channel transition lives at the same per-channel
    level. This keeps one channel's evidence from promoting or degrading a
    different channel.
    """

    channel: str
    state: str
    card_id: int
    template_name: str
    template_ordinal: int
    interval_days: int
    lapses_total: int
    lapses_last_30_days: int
    age_days: int
    is_suspended: bool

    first_lifecycle_review_id: int | None = None
    latest_lifecycle_review_id: int | None = None
    latest_lapse_review_id: int | None = None
    state_episode_id: str = ""
    state_entered_at: str = ""
    first_lifecycle_review_after_state_entry_id: int | None = None
    first_lapse_after_state_entry_id: int | None = None
    assessments: tuple[LifecycleAssessment, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitProgress:
    """
    Unit-level snapshot consumed by reconciliation logic.

    Per-channel transition gates live in ChannelProgress. Selective failed
    channels are a reconciliation result, not duplicated as an input field.
    """

    unit_key: str
    channels: tuple[ChannelProgress, ...] = ()
    all_active_channels_mastered_at: str = ""
    has_leech_tag: bool = False


@dataclass(frozen=True, slots=True)
class PlannedTransition:
    """One deterministic, unmaterialized per-channel lifecycle transition."""

    channel: str
    from_state: str
    to_state: str
    trigger: str
    from_episode_id: str
    evidence: dict[str, Any]
    transition_id: str
    transition_group_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    """Pure one-step T9 reconciliation output with no persistence effects."""

    unit_key: str
    transitions: tuple[PlannedTransition, ...] = ()
    suspend_card_ids: tuple[int, ...] = ()
    reactivation_required_card_ids: tuple[int, ...] = ()
    leech_rescue_channels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconcileRunResult:
    """Materialization/recovery outcome for one automatic reconciliation run."""

    unit_key: str
    committed_transition_ids: tuple[str, ...] = ()
    recovered_transition_ids: tuple[str, ...] = ()
    aborted_transition_ids: tuple[str, ...] = ()
    reactivation_required_card_ids: tuple[int, ...] = ()
    leech_rescue_channels: tuple[str, ...] = ()


# ============================================================
# 6. ENCOUNTER DATA
# ============================================================


@dataclass(frozen=True, slots=True)
class EncounterResult:
    """Monthly corpus-scan result for one Unit."""

    unit_key: str
    count: int
    source: str
    month: str


# ============================================================
# 7. JUDGE RESULTS
# ============================================================


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """
    Structured text/judge result.

    Deliberately has no generic band_score field.
    """

    unit_key: str
    passed: bool
    violations: tuple[Violation, ...] = ()
    model_id: str = ""
    model_version: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """Structured result after STT + scripted speech assessment."""

    unit_key: str
    transcript: str
    passed: bool
    model_id: str
    model_version: str
    violations: tuple[Violation, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
