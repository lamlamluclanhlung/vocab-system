"""Freeze the pure T11/T12 shared assessment contracts."""

import re
from dataclasses import FrozenInstanceError, fields

import pytest

from vocab.contracts import (
    ASSESSMENT_ABSTAIN_REASON_CODES,
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_AUTHORITY_KINDS,
    ASSESSMENT_FAILURE_CODES_BY_CHANNEL,
    ASSESSMENT_OMITTED_REASON_CODES,
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
    ASSESSMENT_OUTCOMES,
    ASSESSMENT_PRODUCTIVE_PRESENCE_CHANNELS,
    ASSESSMENT_PROVENANCE_STAGES,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    CHANNELS,
    COGNITIVE_STIMULUS_NORMALIZATION_FORM,
    HUMAN_REVIEW_DECISIONS,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    TRANSCRIPTION_STATUSES,
)
from vocab.models import (
    JudgeResult,
    LifecycleAssessment,
    SpeechResult,
    T11AssessmentResult,
)


def field_names(model_type: type[object]) -> tuple[str, ...]:
    return tuple(item.name for item in fields(model_type))


def test_assessment_outcomes_are_exact_and_ordered() -> None:
    assert ASSESSMENT_OUTCOME_PASS == "PASS"
    assert ASSESSMENT_OUTCOME_FAIL == "FAIL"
    assert ASSESSMENT_OUTCOME_OMITTED == "OMITTED"
    assert ASSESSMENT_OUTCOME_ABSTAIN == "ABSTAIN"
    assert ASSESSMENT_OUTCOMES == (
        ASSESSMENT_OUTCOME_PASS,
        ASSESSMENT_OUTCOME_FAIL,
        ASSESSMENT_OUTCOME_OMITTED,
        ASSESSMENT_OUTCOME_ABSTAIN,
    )


def test_assessment_task_kinds_align_exactly_with_channels() -> None:
    assert ASSESSMENT_TASK_KIND_BY_CHANNEL == {
        "R": "reading_comprehension",
        "L": "listening_comprehension",
        "W": "written_production",
        "S": "spoken_production",
    }
    assert tuple(ASSESSMENT_TASK_KIND_BY_CHANNEL) == CHANNELS


def test_productive_presence_channels_are_exact() -> None:
    assert ASSESSMENT_PRODUCTIVE_PRESENCE_CHANNELS == ("W", "S")


def test_failure_codes_are_exact_per_channel() -> None:
    assert ASSESSMENT_FAILURE_CODES_BY_CHANNEL == {
        "R": ("wrong_meaning",),
        "L": ("wrong_interpretation",),
        "W": (
            "semantic_misuse",
            "collocation_misuse",
            "form_misuse",
        ),
        "S": (
            "semantic_misuse",
            "collocation_misuse",
            "form_misuse",
        ),
    }


def test_omitted_reasons_are_exact() -> None:
    assert ASSESSMENT_OMITTED_REASON_CODES == ("target_absent",)


def test_abstain_reasons_are_exact_and_ordered() -> None:
    assert ASSESSMENT_ABSTAIN_REASON_CODES == (
        "off_topic",
        "refusal",
        "explicit_skip",
        "no_response",
        "insufficient_lexical_evidence",
        "response_unintelligible",
        "audio_unusable",
        "transcription_uncertain",
        "transcription_failed",
        "semantic_uncertainty",
        "reviewer_rejected",
        "invalid_artifact",
        "infrastructure_failure",
    )


def test_transcription_statuses_are_exact() -> None:
    assert TRANSCRIPTION_STATUSES == ("SUCCESS", "UNCERTAIN", "FAILED")


def test_human_review_decisions_are_exact() -> None:
    assert HUMAN_REVIEW_DECISIONS == ("APPROVE", "REJECT")


def test_authority_kinds_are_exact() -> None:
    assert ASSESSMENT_AUTHORITY_KINDS == (
        "semantic_model",
        "deterministic_gate",
        "policy",
        "human_reviewer",
    )


def test_provenance_stages_are_exact_and_ordered() -> None:
    assert ASSESSMENT_PROVENANCE_STAGES == (
        "presence_gate",
        "transcription",
        "semantic_judge",
        "human_review",
        "policy",
    )


def test_t12_assessment_producer_identity_is_exact() -> None:
    assert T12_ASSESSMENT_PRODUCER_ID == "t12-assessment"
    assert T12_ASSESSMENT_PRODUCER_VERSION == 1
    assert type(T12_ASSESSMENT_PRODUCER_VERSION) is int


@pytest.mark.parametrize(
    ("pattern", "prefix"),
    (
        (ASSESSMENT_STIMULUS_REF_PATTERN, "stimulus:v1:"),
        (ASSESSMENT_ATTEMPT_ID_PATTERN, "attempt:v1:"),
        (ASSESSMENT_ARTIFACT_REF_PATTERN, "sha256:"),
    ),
)
def test_assessment_identity_patterns_accept_lowercase_sha256(
    pattern: str,
    prefix: str,
) -> None:
    assert re.fullmatch(pattern, prefix + ("0123456789abcdef" * 4))


@pytest.mark.parametrize(
    ("pattern", "prefix", "wrong_prefix"),
    (
        (
            ASSESSMENT_STIMULUS_REF_PATTERN,
            "stimulus:v1:",
            "attempt:v1:",
        ),
        (
            ASSESSMENT_ATTEMPT_ID_PATTERN,
            "attempt:v1:",
            "stimulus:v1:",
        ),
        (
            ASSESSMENT_ARTIFACT_REF_PATTERN,
            "sha256:",
            "artifact:v1:",
        ),
    ),
)
def test_assessment_identity_patterns_reject_noncanonical_values(
    pattern: str,
    prefix: str,
    wrong_prefix: str,
) -> None:
    lowercase_digest = "a" * 64
    assert re.fullmatch(pattern, prefix + ("A" * 64)) is None
    assert re.fullmatch(pattern, prefix + ("a" * 63)) is None
    assert re.fullmatch(pattern, prefix + ("a" * 65)) is None
    assert re.fullmatch(pattern, wrong_prefix + lowercase_digest) is None


def test_cognitive_stimulus_normalization_form_is_exact() -> None:
    assert COGNITIVE_STIMULUS_NORMALIZATION_FORM == "NFKC"


def test_t11_assessment_result_field_shape_is_exact() -> None:
    assert field_names(T11AssessmentResult) == (
        "unit_key",
        "channel",
        "outcome",
        "failure_code",
        "reason_code",
    )


def test_t11_assessment_result_defaults_are_empty() -> None:
    result = T11AssessmentResult(
        unit_key="subtle::small-difference",
        channel="R",
        outcome=ASSESSMENT_OUTCOME_PASS,
    )
    assert result.failure_code == ""
    assert result.reason_code == ""


def test_t11_assessment_result_is_frozen_and_slotted() -> None:
    result = T11AssessmentResult(
        unit_key="subtle::small-difference",
        channel="R",
        outcome=ASSESSMENT_OUTCOME_PASS,
    )
    with pytest.raises(FrozenInstanceError):
        result.outcome = ASSESSMENT_OUTCOME_FAIL  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
    assert T11AssessmentResult.__slots__ == (
        "unit_key",
        "channel",
        "outcome",
        "failure_code",
        "reason_code",
    )


@pytest.mark.parametrize(
    ("outcome", "expected_passed"),
    (
        (ASSESSMENT_OUTCOME_PASS, True),
        (ASSESSMENT_OUTCOME_FAIL, False),
        (ASSESSMENT_OUTCOME_OMITTED, False),
        (ASSESSMENT_OUTCOME_ABSTAIN, False),
    ),
)
def test_t11_assessment_result_passed_is_derived(
    outcome: str,
    expected_passed: bool,
) -> None:
    result = T11AssessmentResult(
        unit_key="subtle::small-difference",
        channel="R",
        outcome=outcome,
    )
    assert result.passed is expected_passed
    assert "passed" not in field_names(T11AssessmentResult)


def test_t12_owned_fields_are_absent_from_t11_assessment_result() -> None:
    assert set(field_names(T11AssessmentResult)).isdisjoint(
        {
            "task_kind",
            "attempt_id",
            "assessment_id",
            "stimulus_ref",
            "presented_stimulus_ref",
            "novel",
            "session_id",
            "item_ordinal",
            "provenance",
            "model_id",
            "model_version",
            "event",
            "payload",
        }
    )


def test_historical_judge_result_shape_is_unchanged() -> None:
    assert field_names(JudgeResult) == (
        "unit_key",
        "passed",
        "violations",
        "model_id",
        "model_version",
        "evidence",
    )


def test_historical_speech_result_shape_is_unchanged() -> None:
    assert field_names(SpeechResult) == (
        "unit_key",
        "transcript",
        "passed",
        "model_id",
        "model_version",
        "violations",
        "evidence",
    )


def test_lifecycle_assessment_shape_is_unchanged() -> None:
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
