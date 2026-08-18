"""Freeze structural alignment and fail-closed invariants for T1."""

import re
from dataclasses import fields

from vocab.contracts import (
    CARD_TEMPLATE_NAMES,
    CHANNEL_BY_TEMPLATE_NAME,
    EVENT_REQUIRED_FIELDS,
    NOTE_FIELDS,
    STATE_FIELDS,
    STATE_UNKNOWN,
    UNIT_KEY_PATTERN,
    UNIT_KEY_SEPARATOR,
)
from vocab.models import ChannelProgress, ForgeCandidate, UnitProgress, VocabUnit


def make_unit() -> VocabUnit:
    return VocabUnit(
        unit_key="pose-a-threat-to::create-danger",
        lemma="pose a threat to",
        lemma_slug="pose-a-threat-to",
        sense_slug="create-danger",
        unit_type="chunk",
        Target_R="1",
        state_R="NEW",
    )


def test_note_fields_match_vocab_unit_serialization_exactly() -> None:
    unit = make_unit()
    assert tuple(unit.to_note_fields().keys()) == NOTE_FIELDS


def test_note_fields_are_unique() -> None:
    assert len(NOTE_FIELDS) == len(set(NOTE_FIELDS))


def test_aggregate_state_is_not_persisted() -> None:
    assert "state" not in NOTE_FIELDS
    assert all(field_name in NOTE_FIELDS for field_name in STATE_FIELDS)


def test_unit_key_is_composed_from_approved_slugs() -> None:
    candidate = ForgeCandidate(
        lemma="pose a threat to",
        lemma_slug="pose-a-threat-to",
        sense_slug="create-danger",
        source_ref="dictionary:test",
        source_sentence="This could pose a threat to local wildlife.",
    )

    assert candidate.proposed_unit_key() == (
        "pose-a-threat-to" + UNIT_KEY_SEPARATOR + "create-danger"
    )


def test_surface_lemma_is_not_used_as_slug() -> None:
    candidate = ForgeCandidate(
        lemma="Notwithstanding",
        lemma_slug="notwithstanding",
        sense_slug="despite-formal",
        source_ref="dictionary:test",
        source_sentence="Notwithstanding the delay, the project continued.",
    )

    assert candidate.proposed_unit_key() == "notwithstanding::despite-formal"


def test_valid_composed_unit_key_matches_contract_pattern() -> None:
    candidate = ForgeCandidate(
        lemma="pose a threat to",
        lemma_slug="pose-a-threat-to",
        sense_slug="create-danger",
        source_ref="dictionary:test",
        source_sentence="This could pose a threat to local wildlife.",
    )

    assert re.fullmatch(UNIT_KEY_PATTERN, candidate.proposed_unit_key()) is not None


def test_invalid_unit_key_slugs_do_not_match_contract_pattern() -> None:
    invalid_keys = (
        "Notwithstanding::despite-formal",
        "subtle slug::small-difference",
        "subtle::small difference",
    )

    for key in invalid_keys:
        assert re.fullmatch(UNIT_KEY_PATTERN, key) is None


def test_derived_state_uses_enabled_channels_only() -> None:
    unit = VocabUnit(
        unit_key="subtle::small-difference",
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        Target_R="1",
        Target_S="1",
        state_R="MASTERED",
        state_S="LEARNING",
    )

    assert unit.derived_state() == "LEARNING"


def test_derived_state_ignores_disabled_channel_state() -> None:
    unit = VocabUnit(
        unit_key="subtle::small-difference",
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        Target_R="1",
        Target_L="",
        state_R="MASTERED",
        state_L="NEW",
    )

    assert unit.derived_state() == "MASTERED"


def test_derived_state_fails_closed_for_invalid_active_channel_state() -> None:
    for invalid_state in ("", "ZZZ"):
        unit = VocabUnit(
            unit_key="subtle::small-difference",
            lemma="subtle",
            lemma_slug="subtle",
            sense_slug="small-difference",
            unit_type="word",
            Target_R="1",
            Target_S="1",
            state_R=invalid_state,
            state_S="MASTERED",
        )

        assert unit.derived_state() == STATE_UNKNOWN


def test_default_vocab_unit_does_not_enable_state_without_target() -> None:
    unit = VocabUnit(
        unit_key="subtle::small-difference",
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
    )

    assert unit.Target_R == ""
    assert unit.state_R == ""
    assert unit.derived_state() == ""


def test_event_envelope_requires_day() -> None:
    required = {"v", "ts", "day", "event", "unit_key", "payload"}
    assert required.issubset(EVENT_REQUIRED_FIELDS)


def test_card_template_names_map_stably_to_channels() -> None:
    assert CARD_TEMPLATE_NAMES == ("R", "L", "W", "S")
    assert tuple(CHANNEL_BY_TEMPLATE_NAME) == CARD_TEMPLATE_NAMES
    assert CHANNEL_BY_TEMPLATE_NAME == {"R": "R", "L": "L", "W": "W", "S": "S"}


def test_per_channel_transition_gate_data_lives_on_channel_progress() -> None:
    channel_field_names = {item.name for item in fields(ChannelProgress)}
    unit_field_names = {item.name for item in fields(UnitProgress)}

    per_channel_gate_fields = {
        "session_passes_consecutive",
        "last_session_date",
        "last_session_result",
        "encountered_and_failed",
        "corpus_misuse_detected",
    }

    assert per_channel_gate_fields.issubset(channel_field_names)
    assert per_channel_gate_fields.isdisjoint(unit_field_names)
    assert "failed_channels" not in unit_field_names
