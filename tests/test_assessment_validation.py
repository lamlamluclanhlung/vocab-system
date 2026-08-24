"""Test deterministic T11 assessment-result validation."""

import pytest

from vocab.contracts import (
    ASSESSMENT_ABSTAIN_REASON_CODES,
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
    CHANNELS,
    T11_ASSESSMENT_RESULT_VIOLATION_CODES,
)
from vocab.models import T11AssessmentResult
from vocab.validators import validate_t11_assessment_result


def assessment_result(**overrides: object) -> T11AssessmentResult:
    values: dict[str, object] = {
        "unit_key": "subtle::small-difference",
        "channel": "R",
        "outcome": ASSESSMENT_OUTCOME_PASS,
        "failure_code": "",
        "reason_code": "",
    }
    values.update(overrides)
    return T11AssessmentResult(**values)  # type: ignore[arg-type]


def test_assessment_violation_inventory_is_exact_and_ordered() -> None:
    assert T11_ASSESSMENT_RESULT_VIOLATION_CODES == (
        "A_UNIT_KEY_INVALID",
        "A_CHANNEL_INVALID",
        "A_OUTCOME_INVALID",
        "A_OMITTED_CHANNEL_INVALID",
        "A_FAILURE_CODE_FORBIDDEN",
        "A_FAIL_FAILURE_CODE_INVALID",
        "A_REASON_CODE_FORBIDDEN",
        "A_OMITTED_REASON_CODE_INVALID",
        "A_ABSTAIN_REASON_CODE_INVALID",
    )


@pytest.mark.parametrize("channel", CHANNELS)
def test_pass_is_valid_for_every_channel(channel: str) -> None:
    result = assessment_result(channel=channel)

    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize(
    ("channel", "failure_code"),
    (
        ("R", "wrong_meaning"),
        ("L", "wrong_interpretation"),
        ("W", "semantic_misuse"),
        ("W", "collocation_misuse"),
        ("W", "form_misuse"),
        ("S", "semantic_misuse"),
        ("S", "collocation_misuse"),
        ("S", "form_misuse"),
    ),
)
def test_fail_is_valid_for_each_channel_inventory(
    channel: str,
    failure_code: str,
) -> None:
    result = assessment_result(
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code=failure_code,
    )

    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize("channel", ("W", "S"))
def test_omitted_is_valid_for_productive_channels(channel: str) -> None:
    result = assessment_result(
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        reason_code="target_absent",
    )

    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("reason_code", ASSESSMENT_ABSTAIN_REASON_CODES)
def test_each_abstain_reason_is_valid_for_every_channel(
    channel: str,
    reason_code: str,
) -> None:
    result = assessment_result(
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code=reason_code,
    )

    assert validate_t11_assessment_result(result) == ()


def test_malformed_unit_key_is_invalid_independently() -> None:
    result = assessment_result(unit_key="Bad Key")

    assert validate_t11_assessment_result(result) == (
        "A_UNIT_KEY_INVALID",
    )


def test_unknown_channel_is_invalid() -> None:
    result = assessment_result(channel="X")

    assert validate_t11_assessment_result(result) == (
        "A_CHANNEL_INVALID",
    )


def test_unknown_outcome_suppresses_all_outcome_specific_checks() -> None:
    result = assessment_result(
        outcome="UNKNOWN",
        failure_code="wrong_meaning",
        reason_code="no_response",
    )

    assert validate_t11_assessment_result(result) == (
        "A_OUTCOME_INVALID",
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        (
            {"failure_code": "wrong_meaning"},
            ("A_FAILURE_CODE_FORBIDDEN",),
        ),
        (
            {"reason_code": "no_response"},
            ("A_REASON_CODE_FORBIDDEN",),
        ),
        (
            {
                "failure_code": "wrong_meaning",
                "reason_code": "no_response",
            },
            (
                "A_FAILURE_CODE_FORBIDDEN",
                "A_REASON_CODE_FORBIDDEN",
            ),
        ),
    ),
)
def test_pass_forbids_failure_and_reason_codes(
    overrides: dict[str, object],
    expected: tuple[str, ...],
) -> None:
    result = assessment_result(**overrides)

    assert validate_t11_assessment_result(result) == expected


def test_fail_requires_a_failure_code() -> None:
    result = assessment_result(outcome=ASSESSMENT_OUTCOME_FAIL)

    assert validate_t11_assessment_result(result) == (
        "A_FAIL_FAILURE_CODE_INVALID",
    )


@pytest.mark.parametrize(
    ("channel", "failure_code"),
    (
        ("R", "wrong_interpretation"),
        ("L", "wrong_meaning"),
        ("W", "wrong_meaning"),
        ("S", "wrong_interpretation"),
    ),
)
def test_fail_rejects_another_channels_failure_code(
    channel: str,
    failure_code: str,
) -> None:
    result = assessment_result(
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code=failure_code,
    )

    assert validate_t11_assessment_result(result) == (
        "A_FAIL_FAILURE_CODE_INVALID",
    )


def test_fail_forbids_reason_and_reports_independent_failure_error() -> None:
    result = assessment_result(
        outcome=ASSESSMENT_OUTCOME_FAIL,
        reason_code="no_response",
    )

    assert validate_t11_assessment_result(result) == (
        "A_FAIL_FAILURE_CODE_INVALID",
        "A_REASON_CODE_FORBIDDEN",
    )


@pytest.mark.parametrize("channel", ("R", "L"))
def test_omitted_is_invalid_for_nonproductive_channels(channel: str) -> None:
    result = assessment_result(
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        reason_code="target_absent",
    )

    assert validate_t11_assessment_result(result) == (
        "A_OMITTED_CHANNEL_INVALID",
    )


@pytest.mark.parametrize("reason_code", ("", "no_response"))
def test_omitted_requires_exact_target_absent_reason(
    reason_code: str,
) -> None:
    result = assessment_result(
        channel="W",
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        reason_code=reason_code,
    )

    assert validate_t11_assessment_result(result) == (
        "A_OMITTED_REASON_CODE_INVALID",
    )


def test_omitted_forbids_failure_code() -> None:
    result = assessment_result(
        channel="W",
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        failure_code="semantic_misuse",
        reason_code="target_absent",
    )

    assert validate_t11_assessment_result(result) == (
        "A_FAILURE_CODE_FORBIDDEN",
    )


@pytest.mark.parametrize("reason_code", ("", "target_absent", "unknown"))
def test_abstain_requires_a_closed_abstain_reason(reason_code: str) -> None:
    result = assessment_result(
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code=reason_code,
    )

    assert validate_t11_assessment_result(result) == (
        "A_ABSTAIN_REASON_CODE_INVALID",
    )


def test_abstain_forbids_failure_code() -> None:
    result = assessment_result(
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        failure_code="wrong_meaning",
        reason_code="no_response",
    )

    assert validate_t11_assessment_result(result) == (
        "A_FAILURE_CODE_FORBIDDEN",
    )


def test_invalid_channel_suppresses_fail_inventory_check() -> None:
    result = assessment_result(
        channel="X",
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code="wrong_meaning",
    )

    assert validate_t11_assessment_result(result) == (
        "A_CHANNEL_INVALID",
    )


def test_invalid_channel_does_not_suppress_independent_fail_reason_check() -> None:
    result = assessment_result(
        channel="X",
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code="wrong_meaning",
        reason_code="no_response",
    )

    assert validate_t11_assessment_result(result) == (
        "A_CHANNEL_INVALID",
        "A_REASON_CODE_FORBIDDEN",
    )


def test_invalid_channel_suppresses_omitted_channel_check_only() -> None:
    result = assessment_result(
        channel="X",
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        failure_code="unexpected",
        reason_code="wrong",
    )

    assert validate_t11_assessment_result(result) == (
        "A_CHANNEL_INVALID",
        "A_FAILURE_CODE_FORBIDDEN",
        "A_OMITTED_REASON_CODE_INVALID",
    )


def test_violations_follow_global_order_without_duplicates() -> None:
    result = assessment_result(
        unit_key="Bad Key",
        channel="R",
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        failure_code="wrong_meaning",
        reason_code="wrong",
    )

    actual = validate_t11_assessment_result(result)
    expected = tuple(
        code
        for code in T11_ASSESSMENT_RESULT_VIOLATION_CODES
        if code in actual
    )

    assert actual == (
        "A_UNIT_KEY_INVALID",
        "A_OMITTED_CHANNEL_INVALID",
        "A_FAILURE_CODE_FORBIDDEN",
        "A_OMITTED_REASON_CODE_INVALID",
    )
    assert actual == expected
    assert len(actual) == len(set(actual))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        (
            {"unit_key": None},
            ("A_UNIT_KEY_INVALID",),
        ),
        (
            {"channel": None},
            ("A_CHANNEL_INVALID",),
        ),
        (
            {
                "outcome": None,
                "failure_code": "wrong_meaning",
                "reason_code": "no_response",
            },
            ("A_OUTCOME_INVALID",),
        ),
        (
            {"failure_code": None},
            ("A_FAILURE_CODE_FORBIDDEN",),
        ),
        (
            {
                "outcome": ASSESSMENT_OUTCOME_FAIL,
                "failure_code": None,
            },
            ("A_FAIL_FAILURE_CODE_INVALID",),
        ),
        (
            {"reason_code": None},
            ("A_REASON_CODE_FORBIDDEN",),
        ),
        (
            {
                "channel": "W",
                "outcome": ASSESSMENT_OUTCOME_OMITTED,
                "reason_code": None,
            },
            ("A_OMITTED_REASON_CODE_INVALID",),
        ),
        (
            {
                "outcome": ASSESSMENT_OUTCOME_ABSTAIN,
                "reason_code": None,
            },
            ("A_ABSTAIN_REASON_CODE_INVALID",),
        ),
    ),
)
def test_runtime_wrong_field_types_fail_closed(
    overrides: dict[str, object],
    expected: tuple[str, ...],
) -> None:
    result = assessment_result(**overrides)

    assert validate_t11_assessment_result(result) == expected


def test_validation_is_deterministic_and_does_not_mutate_input() -> None:
    result = assessment_result(
        unit_key="Bad Key",
        channel="R",
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        failure_code="wrong_meaning",
        reason_code="wrong",
    )
    before = (
        result.unit_key,
        result.channel,
        result.outcome,
        result.failure_code,
        result.reason_code,
        result.passed,
    )

    first = validate_t11_assessment_result(result)
    second = validate_t11_assessment_result(result)

    assert first == second
    assert (
        result.unit_key,
        result.channel,
        result.outcome,
        result.failure_code,
        result.reason_code,
        result.passed,
    ) == before
