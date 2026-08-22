"""Freeze the approved T9 v2 reconciliation design without runtime behavior."""

from dataclasses import fields

from vocab.card_contract import (
    FORBIDDEN_NORMAL_REVIEW_FIELDS,
    GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME,
)
from vocab.contracts import (
    ANKI_QUEUE_SUSPENDED,
    AUDIO_FIELDS,
    DORMANT_CLEAR_FIELDS,
    DORMANT_DELETE_NOTE,
    DORMANT_PRESERVE_REVLOG,
    EVENT_PAYLOAD_REQUIRED_FIELDS,
    EVENT_SCHEMA_VERSION,
    EVENT_TYPES,
    INITIAL_NEW_EPISODE_PREFIX,
    LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS,
    LIFECYCLE_SECONDS_PER_DAY,
    NOTE_FIELDS,
    RELAPSE_REACTIVATE_FAILED_CHANNEL_ONLY,
    RESERVED_EVENT_TYPES,
    REVLOG_EASE_AGAIN,
    REVLOG_LIFECYCLE_TYPES,
    REVLOG_TYPE_CRAM,
    REVLOG_TYPE_LEARNING,
    REVLOG_TYPE_RELEARNING,
    REVLOG_TYPE_REVIEW,
    STATE_TRANSITIONS,
    STATE_TRIGGERS,
    STABLE_ZERO_LAPSE_WINDOW_DAYS,
    T9_AUTO_UNSUSPEND,
    T9_LEECH_AUTOCREATE_VISUAL_CUE,
    T9_LEECH_AUTO_TRANSITION,
    T9_DORMANCY_GROUP_KIND,
    T9_STATE_OPTIONAL_PAYLOAD_FIELDS,
    T9_STATE_PHASES,
    T9_STATE_REQUIRED_PAYLOAD_FIELDS,
    T9_UNSUSPEND_REQUIRES_HUMAN_CONFIRMATION,
)
from vocab.media_contract import (
    NORMAL_REVIEW_AUDIO_FIELD,
    RESERVED_AUDIO_FIELDS,
)
from vocab.models import (
    ChannelProgress,
    LifecycleAssessment,
    UnitProgress,
    VocabUnit,
)


def field_names(model_type: type[object]) -> tuple[str, ...]:
    return tuple(item.name for item in fields(model_type))


def test_revlog_and_suspension_observation_constants_are_exact() -> None:
    assert REVLOG_TYPE_LEARNING == 0
    assert REVLOG_TYPE_REVIEW == 1
    assert REVLOG_TYPE_RELEARNING == 2
    assert REVLOG_TYPE_CRAM == 3
    assert REVLOG_LIFECYCLE_TYPES == (0, 1, 2)
    assert REVLOG_EASE_AGAIN == 1
    assert ANKI_QUEUE_SUSPENDED == -1
    assert LIFECYCLE_SECONDS_PER_DAY == 86400


def test_dormancy_retains_artifacts_notes_and_revlog() -> None:
    assert DORMANT_CLEAR_FIELDS == ()
    assert DORMANT_DELETE_NOTE is False
    assert DORMANT_PRESERVE_REVLOG is True


def test_t9_state_trigger_vocabulary_is_closed_and_ordered() -> None:
    assert STATE_TRIGGERS == (
        "FIRST_REVIEW",
        "STABILITY_GATE",
        "REVIEW_LAPSE",
        "MASTERY_ASSESSMENT_PASS",
        "ASSESSMENT_FAIL",
        "DORMANCY_ELAPSED",
        "RELAPSE_REVIEW",
    )


def test_t9_state_journal_phases_and_payload_shape_are_exact() -> None:
    assert T9_STATE_PHASES == (
        "PREPARE",
        "COMMIT",
        "ABORT",
    )
    assert T9_STATE_REQUIRED_PAYLOAD_FIELDS == (
        "channel",
        "from",
        "to",
        "trigger",
        "transition_id",
        "from_episode_id",
        "phase",
        "evidence",
    )
    assert T9_STATE_OPTIONAL_PAYLOAD_FIELDS == (
        "transition_group_id",
    )
    assert INITIAL_NEW_EPISODE_PREFIX == "initial-new:"
    assert T9_DORMANCY_GROUP_KIND == "DORMANCY"


def test_stable_zero_lapse_window_is_frozen() -> None:
    assert STABLE_ZERO_LAPSE_WINDOW_DAYS == 30


def test_t9_reactivation_and_leech_policies_are_exact() -> None:
    assert RELAPSE_REACTIVATE_FAILED_CHANNEL_ONLY is True
    assert T9_AUTO_UNSUSPEND is False
    assert T9_UNSUSPEND_REQUIRES_HUMAN_CONFIRMATION is True
    assert T9_LEECH_AUTO_TRANSITION is False
    assert T9_LEECH_AUTOCREATE_VISUAL_CUE is False


def test_lifecycle_assessment_field_shape_is_exact() -> None:
    assert field_names(LifecycleAssessment) == (
        "channel",
        "passed",
        "assessment_id",
        "stimulus_ref",
        "novel",
        "ts",
        "model_id",
        "model_version",
    )


def test_channel_progress_field_shape_is_exact() -> None:
    assert field_names(ChannelProgress) == (
        "channel",
        "state",
        "card_id",
        "template_name",
        "template_ordinal",
        "interval_days",
        "lapses_total",
        "lapses_last_30_days",
        "age_days",
        "is_suspended",
        "first_lifecycle_review_id",
        "latest_lifecycle_review_id",
        "latest_lapse_review_id",
        "state_episode_id",
        "assessments",
    )


def test_unit_progress_field_shape_is_exact() -> None:
    assert field_names(UnitProgress) == (
        "unit_key",
        "channels",
        "all_active_channels_mastered_at",
        "has_leech_tag",
    )


def test_no_aggregate_lifecycle_state_is_persisted() -> None:
    assert "state" not in NOTE_FIELDS
    assert "state" not in field_names(VocabUnit)
    assert "state" not in field_names(UnitProgress)


def test_d31_reserved_audio_contract_is_unchanged() -> None:
    assert AUDIO_FIELDS == (
        "audio_1",
        "audio_2",
        "audio_3",
    )
    assert "audio_2" in NOTE_FIELDS
    assert "audio_3" in NOTE_FIELDS
    assert NORMAL_REVIEW_AUDIO_FIELD == "audio_1"
    assert RESERVED_AUDIO_FIELDS == (
        "audio_2",
        "audio_3",
    )
    assert all(
        field_name in FORBIDDEN_NORMAL_REVIEW_FIELDS
        for field_name in RESERVED_AUDIO_FIELDS
    )
    assert GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME["L"] == (
        "Target_L",
        "audio_1",
    )


def test_state_transition_graph_is_unchanged() -> None:
    assert STATE_TRANSITIONS == (
        ("NEW", "LEARNING"),
        ("LEARNING", "STABLE"),
        ("STABLE", "MASTERED"),
        ("MASTERED", "DORMANT"),
        ("DORMANT", "RELAPSE"),
        ("RELAPSE", "LEARNING"),
        ("STABLE", "LEARNING"),
        ("MASTERED", "RELAPSE"),
    )


def test_generic_event_v1_decoding_contract_remains_backwards_compatible() -> None:
    assert EVENT_SCHEMA_VERSION == 1
    assert EVENT_PAYLOAD_REQUIRED_FIELDS["STATE"] == (
        "channel",
        "from",
        "to",
        "trigger",
    )
    assert EVENT_PAYLOAD_REQUIRED_FIELDS["JUDGE"] == (
        "channel",
        "passed",
        "model_id",
        "model_version",
    )
    assert LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS == (
        "assessment_id",
        "stimulus_ref",
        "novel",
    )
    assert all(
        field_name not in EVENT_PAYLOAD_REQUIRED_FIELDS["JUDGE"]
        for field_name in LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS
    )


def test_event_vocabulary_is_unchanged_and_review_remains_reserved() -> None:
    assert EVENT_TYPES == (
        "REVIEW",
        "JUDGE",
        "FORGE",
        "STATE",
        "SPEAK",
        "ENCOUNTER",
    )
    assert RESERVED_EVENT_TYPES == ("REVIEW",)
