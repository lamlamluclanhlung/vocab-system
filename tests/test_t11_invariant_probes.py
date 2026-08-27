"""D58 T11-side invariant probes for the human-mediated semantic bridge.

These probes close the deterministic T11 side only. D58 probes that require
T12-owned identity, exposure history, novelty, producer preflight, or crash
recovery remain deferred until T12 exists.
"""

from __future__ import annotations

import json

import pytest

from vocab.contracts import (
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
)
from vocab.human_review import (
    build_human_review,
    import_human_review,
    serialize_human_review,
)
from vocab.models import T11AssessmentResult
from vocab.review_materialization import materialize_reviewed_t11_result
from vocab.semantic_request import build_semantic_request, semantic_request_digest
from vocab.semantic_response import (
    ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    SemanticResponseError,
    import_semantic_response,
)
from vocab.validators import contains_unit, validate_t11_assessment_result


UNIT_KEY = "subtle::small-difference"
LEMMA = "subtle"
UNIT_TYPE = "word"
DEFINITION = "not immediately obvious; a small, hard-to-notice difference"

T12_OWNED_FIELDS = {
    "session_id",
    "item_ordinal",
    "attempt_id",
    "assessment_id",
    "stimulus_ref",
    "presented_stimulus_ref",
    "stimulus_artifact_ref",
    "response_artifact_ref",
    "response_audio_ref",
    "novel",
    "producer",
    "producer_version",
    "reserved_at",
}


def transport_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def make_request(channel: str, task_content: dict[str, str]) -> dict[str, object]:
    return build_semantic_request(
        unit_key=UNIT_KEY,
        lemma=LEMMA,
        unit_type=UNIT_TYPE,
        definition_en=DEFINITION,
        channel=channel,
        task_content=task_content,
    )


def import_proposal(
    request: object,
    *,
    outcome: str,
    failure_code: str = "",
    reason_code: str = "",
    rationale: str,
):
    proposal = {
        "artifact": "vocab.t11.semantic-response",
        "v": 1,
        "request_digest": semantic_request_digest(request),
        "outcome": outcome,
        "failure_code": failure_code,
        "reason_code": reason_code,
        "semantic_rationale": rationale,
    }
    return import_semantic_response(
        transport_bytes(proposal),
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )


def materialize(imported_proposal, *, decision: str = "APPROVE"):
    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision=decision,
    )
    imported_review = import_human_review(
        serialize_human_review(
            review,
            imported_proposal=imported_proposal,
        ),
        imported_proposal=imported_proposal,
    )
    return materialize_reviewed_t11_result(
        imported_proposal=imported_proposal,
        imported_review=imported_review,
    )


SEMANTIC_ANCHORS = (
    (
        "R",
        {
            "passage": "The difference between the two shades was subtle.",
            "question": "What does subtle mean here?",
            "learner_response": "It was a slight difference that was hard to notice.",
        },
        ASSESSMENT_OUTCOME_PASS,
        "",
        "",
        False,
    ),
    (
        "R",
        {
            "passage": "The difference between the two shades was subtle.",
            "question": "What does subtle mean here?",
            "learner_response": "It means the difference was extremely loud and obvious.",
        },
        ASSESSMENT_OUTCOME_FAIL,
        "wrong_meaning",
        "",
        False,
    ),
    (
        "R",
        {
            "passage": "The difference between the two shades was subtle.",
            "question": "What does subtle mean here?",
            "learner_response": "I ate noodles for breakfast.",
        },
        ASSESSMENT_OUTCOME_ABSTAIN,
        "",
        "off_topic",
        False,
    ),
    (
        "L",
        {
            "spoken_script": "The distinction between the proposals was subtle.",
            "question": "How did the speaker describe the distinction?",
            "learner_response": "It was slight and difficult to notice.",
        },
        ASSESSMENT_OUTCOME_PASS,
        "",
        "",
        False,
    ),
    (
        "L",
        {
            "spoken_script": "The distinction between the proposals was subtle.",
            "question": "How did the speaker describe the distinction?",
            "learner_response": "The speaker said it was huge and impossible to miss.",
        },
        ASSESSMENT_OUTCOME_FAIL,
        "wrong_interpretation",
        "",
        False,
    ),
    (
        "W",
        {
            "production_prompt": "Compare two nearly identical results.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "learner_response": "The experiments showed a subtle difference in timing.",
        },
        ASSESSMENT_OUTCOME_PASS,
        "",
        "",
        True,
    ),
    (
        "W",
        {
            "production_prompt": "Compare two nearly identical results.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "learner_response": "The explosion was subtle because it destroyed the building.",
        },
        ASSESSMENT_OUTCOME_FAIL,
        "semantic_misuse",
        "",
        True,
    ),
    (
        "W",
        {
            "production_prompt": "Compare two nearly identical results.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "learner_response": "The report made a subtle loudly difference in tone.",
        },
        ASSESSMENT_OUTCOME_FAIL,
        "collocation_misuse",
        "",
        True,
    ),
    (
        "S",
        {
            "production_prompt": "Describe a small difference between two plans.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "approved_transcript": "There is a subtle difference in their priorities.",
        },
        ASSESSMENT_OUTCOME_PASS,
        "",
        "",
        True,
    ),
    (
        "S",
        {
            "production_prompt": "Describe a small difference between two plans.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "approved_transcript": "The earthquake was subtle because every house collapsed.",
        },
        ASSESSMENT_OUTCOME_FAIL,
        "semantic_misuse",
        "",
        True,
    ),
)


@pytest.mark.parametrize(
    (
        "channel",
        "task_content",
        "outcome",
        "failure_code",
        "reason_code",
        "target_present",
    ),
    SEMANTIC_ANCHORS,
)
def test_d58_semantic_anchors_survive_exact_approve_path(
    channel: str,
    task_content: dict[str, str],
    outcome: str,
    failure_code: str,
    reason_code: str,
    target_present: bool,
) -> None:
    evidence_field = {
        "R": "learner_response",
        "L": "learner_response",
        "W": "learner_response",
        "S": "approved_transcript",
    }[channel]
    evidence = task_content[evidence_field]
    assert contains_unit(evidence, LEMMA, UNIT_TYPE) is target_present

    request = make_request(channel, task_content)
    imported = import_proposal(
        request,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
        rationale="The supplied anchor evidence supports the frozen expected outcome.",
    )
    materialized = materialize(imported)

    assert materialized is imported.assessment_result
    assert (
        materialized.unit_key,
        materialized.channel,
        materialized.outcome,
        materialized.failure_code,
        materialized.reason_code,
    ) == (UNIT_KEY, channel, outcome, failure_code, reason_code)
    assert validate_t11_assessment_result(materialized) == ()


def test_r_and_l_do_not_gain_a_target_presence_gate() -> None:
    for channel, task_content in (
        (
            "R",
            {
                "passage": "The difference was subtle.",
                "question": "What does the word mean here?",
                "learner_response": "It means a slight difference that is hard to notice.",
            },
        ),
        (
            "L",
            {
                "spoken_script": "The difference was subtle.",
                "question": "What did the speaker mean?",
                "learner_response": "It was a slight difference that was hard to notice.",
            },
        ),
    ):
        assert not contains_unit(task_content["learner_response"], LEMMA, UNIT_TYPE)
        result = materialize(
            import_proposal(
                make_request(channel, task_content),
                outcome=ASSESSMENT_OUTCOME_PASS,
                rationale="Correct contextual paraphrase without target repetition.",
            )
        )
        assert result.outcome == ASSESSMENT_OUTCOME_PASS


@pytest.mark.parametrize(
    ("channel", "evidence"),
    (
        ("W", "The two results were almost the same."),
        ("S", "The two plans differ only a little."),
    ),
)
def test_productive_target_absence_is_omitted_and_never_fail(
    channel: str,
    evidence: str,
) -> None:
    assert not contains_unit(evidence, LEMMA, UNIT_TYPE)
    result = T11AssessmentResult(
        unit_key=UNIT_KEY,
        channel=channel,
        outcome=ASSESSMENT_OUTCOME_OMITTED,
        reason_code="target_absent",
    )

    assert result.outcome != ASSESSMENT_OUTCOME_FAIL
    assert result.failure_code == ""
    assert validate_t11_assessment_result(result) == ()


def test_semantic_proposal_cannot_turn_productive_omission_into_semantic_outcome() -> None:
    request = make_request(
        "W",
        {
            "production_prompt": "Compare two nearly identical results.",
            "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
            "learner_response": "The experiments showed a subtle difference in timing.",
        },
    )

    with pytest.raises(SemanticResponseError):
        import_proposal(
            request,
            outcome=ASSESSMENT_OUTCOME_OMITTED,
            reason_code="target_absent",
            rationale="A semantic model must never own OMITTED.",
        )


@pytest.mark.parametrize(
    "reason_code",
    ("transcription_uncertain", "transcription_failed", "audio_unusable"),
)
def test_speech_transcription_uncertainty_is_abstain_not_learner_failure(
    reason_code: str,
) -> None:
    result = T11AssessmentResult(
        unit_key=UNIT_KEY,
        channel="S",
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code=reason_code,
    )

    assert result.outcome not in (
        ASSESSMENT_OUTCOME_FAIL,
        ASSESSMENT_OUTCOME_OMITTED,
    )
    assert result.failure_code == ""
    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize(
    ("channel", "outcome", "failure_code"),
    (
        ("R", ASSESSMENT_OUTCOME_PASS, ""),
        ("R", ASSESSMENT_OUTCOME_FAIL, "wrong_meaning"),
        ("W", ASSESSMENT_OUTCOME_FAIL, "semantic_misuse"),
        ("S", ASSESSMENT_OUTCOME_PASS, ""),
    ),
)
def test_reviewer_rejection_cannot_leave_pass_or_fail_as_accepted_evidence(
    channel: str,
    outcome: str,
    failure_code: str,
) -> None:
    task_content = {
        "R": {
            "passage": "The difference was subtle.",
            "question": "What does subtle mean?",
            "learner_response": "It was slight and hard to notice.",
        },
        "W": {
            "production_prompt": "Compare two results.",
            "semantic_constraints": "Use subtle for a small difference.",
            "learner_response": "The explosion was subtle because it destroyed everything.",
        },
        "S": {
            "production_prompt": "Compare two plans.",
            "semantic_constraints": "Use subtle for a small difference.",
            "approved_transcript": "There is a subtle difference between them.",
        },
    }[channel]
    imported = import_proposal(
        make_request(channel, task_content),
        outcome=outcome,
        failure_code=failure_code,
        rationale="Synthetic probe proposal for exact review rejection behavior.",
    )

    materialized = materialize(imported, decision="REJECT")

    assert materialized is not imported.assessment_result
    assert materialized.outcome == ASSESSMENT_OUTCOME_ABSTAIN
    assert materialized.failure_code == ""
    assert materialized.reason_code == "reviewer_rejected"
    assert validate_t11_assessment_result(materialized) == ()


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(recursive_keys(child))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for child in value:
            keys.update(recursive_keys(child))
        return keys
    return set()


def test_t11_bridge_artifacts_remain_free_of_t12_owned_runtime_fields() -> None:
    request = make_request(
        "R",
        {
            "passage": "Sự khác biệt giữa hai phương án rất subtle.",
            "question": "What does subtle mean here?",
            "learner_response": "It means a slight, hard-to-notice difference.",
        },
    )
    imported = import_proposal(
        request,
        outcome=ASSESSMENT_OUTCOME_PASS,
        rationale="The learner correctly paraphrases the target sense.",
    )
    review = build_human_review(
        imported_proposal=imported,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision="APPROVE",
    )

    assert recursive_keys(request).isdisjoint(T12_OWNED_FIELDS)
    assert recursive_keys(dict(imported.proposal)).isdisjoint(T12_OWNED_FIELDS)
    assert recursive_keys(review).isdisjoint(T12_OWNED_FIELDS)


def test_t11_deterministic_smoke_round_trip_preserves_unicode_and_exact_result() -> None:
    request = make_request(
        "R",
        {
            "passage": "Sự khác biệt giữa hai phương án rất subtle.",
            "question": "Trong ngữ cảnh này, subtle có nghĩa gì?",
            "learner_response": "Khác biệt nhỏ, tinh tế và khó nhận ra ngay.",
        },
    )
    request_digest = semantic_request_digest(request)
    imported = import_proposal(
        request,
        outcome=ASSESSMENT_OUTCOME_PASS,
        rationale="The Vietnamese paraphrase expresses a slight, hard-to-notice difference.",
    )
    result = materialize(imported)

    assert len(request_digest) == 64
    assert request_digest == imported.request_digest
    assert imported.proposal["request_digest"] == request_digest
    assert result is imported.assessment_result
    assert result == T11AssessmentResult(
        unit_key=UNIT_KEY,
        channel="R",
        outcome=ASSESSMENT_OUTCOME_PASS,
    )
