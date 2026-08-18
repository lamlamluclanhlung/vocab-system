import pytest

from vocab.contracts import (
    CONTEXT_VIOLATION_CODES,
    FORGE_VIOLATION_CODES,
)

from vocab.models import VocabUnit
from vocab.validators import (
    contains_unit,
    normalize_tokens,
    validate_forge_unit,
    validate_context_bank,
)

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Don't", ("don't",)),
        ("DON’T", ("don't",)),
        ("state-of-the-art", ("state", "of", "the", "art")),
        ("partial", ("partial",)),
    ],
)
def test_normalize_tokens_is_deterministic(text, expected) -> None:
    assert normalize_tokens(text) == expected


def test_word_matches_complete_token_only() -> None:
    assert contains_unit("This is art.", "art", "word") is True
    assert contains_unit("This is partial.", "art", "word") is False


def test_word_unit_must_be_exactly_one_token() -> None:
    with pytest.raises(ValueError):
        contains_unit("This is state of the art.", "state of the art", "word")


def test_chunk_allows_at_most_two_inserted_tokens_in_total() -> None:
    unit = "pose a threat to"

    assert contains_unit(
        "This may pose a serious threat to stability.",
        unit,
        "chunk",
    ) is True

    assert contains_unit(
        "This may pose a very serious threat to stability.",
        unit,
        "chunk",
    ) is True

    assert contains_unit(
        "This may pose a very immediate serious threat to stability.",
        unit,
        "chunk",
    ) is False


def test_chunk_preserves_target_token_order() -> None:
    assert (
        contains_unit(
            "The threat may pose a risk to the system.",
            "pose a threat to",
            "chunk",
        )
        is False
    )


def test_frame_matches_one_to_six_slot_tokens() -> None:
    unit = "it is ___ that"

    assert contains_unit(
        "It is believed that this works.",
        unit,
        "frame",
    ) is True

    assert contains_unit(
        "It is very widely believed that this works.",
        unit,
        "frame",
    ) is True


def test_frame_rejects_empty_slot() -> None:
    assert contains_unit(
        "It is that simple.",
        "it is ___ that",
        "frame",
    ) is False


def test_frame_rejects_slot_longer_than_six_tokens() -> None:
    assert contains_unit(
        "It is one two three four five six seven that works.",
        "it is ___ that",
        "frame",
    ) is False


@pytest.mark.parametrize(
    "unit",
    [
        "___ depends on",
        "it is ___",
        "it ___ ___ that",
    ],
)
def test_invalid_v0_frame_shape_is_rejected(unit) -> None:
    with pytest.raises(ValueError):
        contains_unit(
            "It is widely believed that this works.",
            unit,
            "frame",
        )

def valid_forge_unit(**overrides) -> VocabUnit:
    values = {
        "unit_key": "pose::threat",
        "lemma": "pose a threat to",
        "lemma_slug": "pose",
        "sense_slug": "threat",
        "unit_type": "chunk",
        "Target_R": "1",
        "Target_L": "",
        "Target_W": "",
        "Target_S": "",
        "state_R": "NEW",
        "state_L": "",
        "state_W": "",
        "state_S": "",
        "register": "neutral",
        "definition_en": "to create a possible danger or problem",
        "source_ref": "dictionary:cambridge:pose-threat",
        "source_sentence": "Climate change may pose a serious threat to food security.",
    }
    values.update(overrides)
    return VocabUnit(**values)


def test_valid_forge_unit_passes_before_context_and_media_generation() -> None:
    unit = valid_forge_unit()

    assert unit.Ctx_1 == ""
    assert unit.audio_1 == ""
    assert validate_forge_unit(unit) == ()


def test_forge_identity_violations_follow_frozen_order() -> None:
    unit = valid_forge_unit(
        unit_key="WRONG",
        lemma_slug="Bad Slug",
        sense_slug="Bad Sense",
    )

    assert validate_forge_unit(unit)[:4] == (
        "F_LEMMA_SLUG_INVALID",
        "F_SENSE_SLUG_INVALID",
        "F_UNIT_KEY_INVALID",
        "F_UNIT_KEY_MISMATCH",
    )


def test_empty_lemma_suppresses_dependent_shape_and_containment_checks() -> None:
    unit = valid_forge_unit(lemma="   ")

    violations = validate_forge_unit(unit)

    assert "F_LEMMA_EMPTY" in violations
    assert "F_UNIT_SHAPE_INVALID" not in violations
    assert "F_SOURCE_UNIT_MISSING" not in violations


def test_invalid_unit_type_suppresses_shape_and_containment_checks() -> None:
    unit = valid_forge_unit(unit_type="phrase")

    violations = validate_forge_unit(unit)

    assert "F_UNIT_TYPE_INVALID" in violations
    assert "F_UNIT_SHAPE_INVALID" not in violations
    assert "F_SOURCE_UNIT_MISSING" not in violations


def test_invalid_unit_shape_becomes_violation_not_exception() -> None:
    unit = valid_forge_unit(
        lemma="one token",
        unit_type="word",
    )

    assert "F_UNIT_SHAPE_INVALID" in validate_forge_unit(unit)


@pytest.mark.parametrize(
    ("field_name", "code"),
    [
        ("Target_R", "F_TARGET_R_INVALID"),
        ("Target_L", "F_TARGET_L_INVALID"),
        ("Target_W", "F_TARGET_W_INVALID"),
        ("Target_S", "F_TARGET_S_INVALID"),
    ],
)
def test_invalid_target_flag_maps_to_channel_code(field_name, code) -> None:
    unit = valid_forge_unit(**{field_name: "yes"})

    violations = validate_forge_unit(unit)

    assert code in violations
    assert "F_NO_TARGET_ENABLED" not in violations


def test_no_target_enabled_requires_all_target_flags_to_be_valid() -> None:
    unit = valid_forge_unit(
        Target_R="",
        state_R="",
    )

    assert "F_NO_TARGET_ENABLED" in validate_forge_unit(unit)


@pytest.mark.parametrize(
    ("field_name", "code"),
    [
        ("state_R", "F_STATE_R_INVALID"),
        ("state_L", "F_STATE_L_INVALID"),
        ("state_W", "F_STATE_W_INVALID"),
        ("state_S", "F_STATE_S_INVALID"),
    ],
)
def test_invalid_state_maps_to_channel_code(field_name, code) -> None:
    unit = valid_forge_unit(**{field_name: "UNKNOWN"})

    assert code in validate_forge_unit(unit)


@pytest.mark.parametrize(
    ("target_field", "state_field", "target_value", "state_value", "code"),
    [
        (
            "Target_R",
            "state_R",
            "1",
            "",
            "F_TARGET_STATE_R_MISMATCH",
        ),
        (
            "Target_L",
            "state_L",
            "",
            "NEW",
            "F_TARGET_STATE_L_MISMATCH",
        ),
        (
            "Target_W",
            "state_W",
            "1",
            "",
            "F_TARGET_STATE_W_MISMATCH",
        ),
        (
            "Target_S",
            "state_S",
            "",
            "NEW",
            "F_TARGET_STATE_S_MISMATCH",
        ),
    ],
)
def test_target_state_presence_is_biconditional(
    target_field,
    state_field,
    target_value,
    state_value,
    code,
) -> None:
    unit = valid_forge_unit(
        **{
            target_field: target_value,
            state_field: state_value,
        }
    )

    assert code in validate_forge_unit(unit)


def test_invalid_target_or_state_suppresses_channel_mismatch_check() -> None:
    unit = valid_forge_unit(
        Target_R="yes",
        state_R="UNKNOWN",
    )

    violations = validate_forge_unit(unit)

    assert "F_TARGET_R_INVALID" in violations
    assert "F_STATE_R_INVALID" in violations
    assert "F_TARGET_STATE_R_MISMATCH" not in violations


def test_register_definition_and_source_ref_are_validated_independently() -> None:
    unit = valid_forge_unit(
        register="formal-ish",
        definition_en="   ",
        source_ref="web:https://example.com",
    )

    violations = validate_forge_unit(unit)

    assert "F_REGISTER_INVALID" in violations
    assert "F_DEFINITION_EMPTY" in violations
    assert "F_SOURCE_REF_INVALID" in violations


def test_empty_source_sentence_suppresses_unit_missing_check() -> None:
    unit = valid_forge_unit(source_sentence="   ")

    violations = validate_forge_unit(unit)

    assert "F_SOURCE_SENTENCE_EMPTY" in violations
    assert "F_SOURCE_UNIT_MISSING" not in violations


def test_source_sentence_must_contain_unit_with_shared_matcher() -> None:
    unit = valid_forge_unit(
        source_sentence="Climate change creates serious risks for food security."
    )

    assert "F_SOURCE_UNIT_MISSING" in validate_forge_unit(unit)


def test_forge_violations_are_returned_in_global_contract_order() -> None:
    unit = valid_forge_unit(
        unit_key="wrong",
        lemma_slug="Bad Slug",
        Target_R="",
        state_R="",
        register="bad-register",
        definition_en="",
        source_ref="bad",
        source_sentence="",
    )

    actual = validate_forge_unit(unit)
    expected = tuple(
        code
        for code in FORGE_VIOLATION_CODES
        if code in actual
    )

    assert actual == expected
    assert len(actual) == len(set(actual))

def valid_context_unit(**overrides) -> VocabUnit:
    values = {
        "unit_key": "art::creative-work",
        "lemma": "art",
        "lemma_slug": "art",
        "sense_slug": "creative-work",
        "unit_type": "word",
        "Target_R": "1",
        "state_R": "NEW",
        "register": "neutral",
        "definition_en": "creative activity that expresses ideas or feelings",
        "source_ref": "dictionary:cambridge:art",
        "source_sentence": (
            "Art alpha beta gamma delta epsilon zeta eta theta iota kappa."
        ),
        "Ctx_1": (
            "Art inspires curious learners through museums and thoughtful "
            "discussion every weekend."
        ),
        "Ctx_2": (
            "Young students explore art while sharing creative ideas during "
            "collaborative classroom projects."
        ),
        "Ctx_3": (
            "Digital artists use art to communicate complex emotions across "
            "different communities online."
        ),
        "Ctx_4": (
            "Public art can transform ordinary spaces into memorable places "
            "for local residents."
        ),
        "Ctx_5": (
            "Studying art encourages patience observation imagination and "
            "careful attention over time."
        ),
    }
    values.update(overrides)
    return VocabUnit(**values)


def test_valid_context_bank_passes() -> None:
    unit = valid_context_unit()

    assert validate_forge_unit(unit) == ()
    assert validate_context_bank(unit) == ()


def test_empty_context_suppresses_dependent_context_checks() -> None:
    unit = valid_context_unit(Ctx_1="   ")

    violations = validate_context_bank(unit)

    assert "C_CTX_1_EMPTY" in violations
    assert "C_CTX_1_UNIT_MISSING" not in violations
    assert "C_CTX_1_TOO_SHORT" not in violations
    assert "C_CTX_1_SOURCE_COPY" not in violations


def test_nonempty_context_must_contain_unit() -> None:
    unit = valid_context_unit(
        Ctx_1=(
            "Music inspires curious learners through museums and thoughtful "
            "discussion every weekend."
        )
    )

    violations = validate_context_bank(unit)

    assert "C_CTX_1_UNIT_MISSING" in violations
    assert "C_CTX_1_TOO_SHORT" not in violations
    assert "C_CTX_1_SOURCE_COPY" not in violations


def test_word_context_must_meet_total_and_residual_length() -> None:
    unit = valid_context_unit(
        Ctx_1="Art helps people learn quickly today."
    )

    assert "C_CTX_1_TOO_SHORT" in validate_context_bank(unit)


def test_chunk_length_requires_six_tokens_beyond_fixed_unit() -> None:
    unit = valid_context_unit(
        unit_key="pose::threat",
        lemma="pose a threat to",
        lemma_slug="pose",
        sense_slug="threat",
        unit_type="chunk",
        source_ref="dictionary:cambridge:pose-threat",
        source_sentence=(
            "Climate change may pose a serious threat to food security."
        ),
        Ctx_1="Teams pose a serious threat to local systems today.",
        Ctx_2=(
            "New policies may pose a growing threat to independent "
            "research institutions worldwide."
        ),
        Ctx_3=(
            "Poor planning can pose a major threat to long term "
            "community development efforts."
        ),
        Ctx_4=(
            "Unexpected delays may pose a serious threat to successful "
            "delivery across regional projects."
        ),
        Ctx_5=(
            "Weak oversight can pose a substantial threat to public "
            "confidence in complex institutions."
        ),
    )

    assert validate_forge_unit(unit) == ()
    assert "C_CTX_1_TOO_SHORT" in validate_context_bank(unit)


def test_contexts_are_distinct_after_normalization() -> None:
    first = (
        "Art inspires curious learners through museums and thoughtful "
        "discussion every weekend."
    )
    second = (
        "ART inspires curious learners through museums, and thoughtful "
        "discussion every weekend!"
    )

    unit = valid_context_unit(
        Ctx_1=first,
        Ctx_2=second,
    )

    violations = validate_context_bank(unit)

    assert violations.count("C_CONTEXTS_NOT_DISTINCT") == 1


def test_source_copy_ratio_exactly_point_six_is_allowed() -> None:
    unit = valid_context_unit(
        Ctx_1=(
            "Art alpha beta gamma delta epsilon zeta novel one two three"
        )
    )

    assert "C_CTX_1_SOURCE_COPY" not in validate_context_bank(unit)


def test_source_copy_ratio_above_point_six_is_rejected() -> None:
    unit = valid_context_unit(
        Ctx_1=(
            "Art alpha beta gamma delta epsilon zeta eta novel one two"
        )
    )

    assert "C_CTX_1_SOURCE_COPY" in validate_context_bank(unit)


def test_source_copy_overlap_uses_token_multisets() -> None:
    unit = valid_context_unit(
        Ctx_1=(
            "Art alpha alpha alpha alpha alpha alpha alpha novel one two"
        )
    )

    # source_sentence contains only one residual "alpha".
    # Correct multiset overlap therefore counts only one shared alpha,
    # rather than treating all seven context copies as shared.
    assert "C_CTX_1_SOURCE_COPY" not in validate_context_bank(unit)


def test_source_copy_subtracts_exactly_one_unit_multiset() -> None:
    unit = valid_context_unit(
        source_sentence=(
            "Art art alpha beta gamma delta epsilon zeta eta theta."
        ),
        Ctx_1=(
            "Art art alpha beta gamma delta epsilon zeta novel one two three"
        ),
    )

    # Exactly one fixed "art" is removed from each side.
    # The second "art" remains residual and contributes to copying:
    # 7 shared residual tokens / 11 context residual tokens > 0.60.
    assert "C_CTX_1_SOURCE_COPY" in validate_context_bank(unit)


def test_frame_slot_content_remains_residual_for_source_copy() -> None:
    unit = valid_context_unit(
        unit_key="it-is::claim-frame",
        lemma="it is ___ that",
        lemma_slug="it-is",
        sense_slug="claim-frame",
        unit_type="frame",
        source_ref="corpus:examples:claim-frame",
        source_sentence=(
            "It is widely believed that alpha beta gamma delta epsilon zeta."
        ),
        Ctx_1="It is widely believed that alpha beta novel one",
        Ctx_2=(
            "It is often argued that careful planning improves long term "
            "team outcomes."
        ),
        Ctx_3=(
            "It is sometimes assumed that regular practice builds confidence "
            "across unfamiliar situations."
        ),
        Ctx_4=(
            "It is generally accepted that clear feedback supports better "
            "learning over time."
        ),
        Ctx_5=(
            "It is increasingly recognized that strong routines improve "
            "consistent performance at work."
        ),
    )

    assert validate_forge_unit(unit) == ()

    # Fixed frame tokens are: it / is / that.
    # "widely believed" belongs to the slot, so it remains residual.
    assert "C_CTX_1_SOURCE_COPY" in validate_context_bank(unit)


def test_empty_contexts_do_not_participate_in_distinctness() -> None:
    unit = valid_context_unit(
        Ctx_1="",
        Ctx_2="",
    )

    violations = validate_context_bank(unit)

    assert "C_CTX_1_EMPTY" in violations
    assert "C_CTX_2_EMPTY" in violations
    assert "C_CONTEXTS_NOT_DISTINCT" not in violations


def test_context_violations_follow_global_contract_order() -> None:
    unit = valid_context_unit(
        Ctx_1="",
        Ctx_2=(
            "Music inspires curious learners through museums and thoughtful "
            "discussion every weekend."
        ),
        Ctx_3=(
            "Art alpha beta gamma delta epsilon zeta eta novel one two"
        ),
    )

    actual = validate_context_bank(unit)
    expected = tuple(
        code
        for code in CONTEXT_VIOLATION_CODES
        if code in actual
    )

    assert actual == expected
    assert len(actual) == len(set(actual))