"""Pure tests for T7 leech deck-option verification."""

from __future__ import annotations

from copy import deepcopy

import pytest

from vocab.contracts import (
    ANKI_LEECH_ACTION,
    ANKI_LEECH_THRESHOLD,
    LEECH_LAPSE_THRESHOLD,
)
from vocab.leech import verify_leech_config


def valid_config() -> dict[str, object]:
    return {
        "id": 1723456789,
        "name": "Runtime preset name is not contractual",
        "lapse": {
            "leechFails": 4,
            "leechAction": 1,
            "otherOption": "ignored",
        },
    }


def codes(config: object) -> tuple[str, ...]:
    return tuple(
        violation.code for violation in verify_leech_config(config)
    )


def test_valid_leech_config_passes() -> None:
    assert verify_leech_config(valid_config()) == ()


@pytest.mark.parametrize("threshold", [8, "4", True])
def test_threshold_mismatch_fails_closed(threshold: object) -> None:
    config = valid_config()
    config["lapse"]["leechFails"] = threshold

    assert codes(config) == ("L7_THRESHOLD_MISMATCH",)


@pytest.mark.parametrize(
    ("action", "expected_code"),
    [
        (0, "L7_ACTION_SUSPEND"),
        (2, "L7_ACTION_UNKNOWN"),
        ("tag_only", "L7_ACTION_UNKNOWN"),
        (True, "L7_ACTION_UNKNOWN"),
    ],
)
def test_action_mismatch_fails_closed(
    action: object,
    expected_code: str,
) -> None:
    config = valid_config()
    config["lapse"]["leechAction"] = action

    assert codes(config) == (expected_code,)


def test_missing_lapse_section_suppresses_dependent_checks() -> None:
    config = valid_config()
    del config["lapse"]

    assert codes(config) == ("L7_LAPSE_SECTION_MISSING",)


@pytest.mark.parametrize("lapse", [None, False, [], "bad"])
def test_non_mapping_lapse_section_fails(lapse: object) -> None:
    config = valid_config()
    config["lapse"] = lapse

    assert codes(config) == ("L7_LAPSE_SECTION_MISSING",)


@pytest.mark.parametrize("config", [None, False, [], "bad"])
def test_non_mapping_config_fails(config: object) -> None:
    assert codes(config) == ("L7_CONFIG_SHAPE_INVALID",)


def test_independent_violations_have_fixed_order() -> None:
    config = valid_config()
    config["lapse"]["leechFails"] = 8
    config["lapse"]["leechAction"] = 0

    assert codes(config) == (
        "L7_THRESHOLD_MISMATCH",
        "L7_ACTION_SUSPEND",
    )


def test_verification_is_pure_and_repeatable() -> None:
    config = valid_config()
    config["lapse"]["leechFails"] = "4"
    original = deepcopy(config)

    first = verify_leech_config(config)
    second = verify_leech_config(config)

    assert config == original
    assert first == second


def test_leech_contract_alignment() -> None:
    assert ANKI_LEECH_THRESHOLD == LEECH_LAPSE_THRESHOLD == 4
    assert ANKI_LEECH_ACTION == "tag_only"
