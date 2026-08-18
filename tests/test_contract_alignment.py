"""Freeze the structural alignment between contracts.py and models.py."""

from vocab.contracts import NOTE_FIELDS, STATE_FIELDS, UNIT_KEY_SEPARATOR
from vocab.models import ForgeCandidate, VocabUnit


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
