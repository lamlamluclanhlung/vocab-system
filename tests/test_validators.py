import pytest

from vocab.validators import contains_unit, normalize_tokens


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