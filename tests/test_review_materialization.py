"""Tests for pure D59 T11 APPROVE/REJECT result materialization."""

from __future__ import annotations

import ast
import copy
import inspect
import json
from dataclasses import replace

import pytest

import vocab.review_materialization as materialization_module
from vocab.contracts import (
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_PASS,
)
from vocab.human_review import (
    ImportedHumanReview,
    build_human_review,
    import_human_review,
    serialize_human_review,
)
from vocab.models import T11AssessmentResult
from vocab.review_materialization import (
    T11MaterializationError,
    materialize_reviewed_t11_result,
)
from vocab.semantic_request import build_semantic_request, semantic_request_digest
from vocab.semantic_response import (
    ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    ImportedSemanticProposal,
    import_semantic_response,
)
from vocab.validators import validate_t11_assessment_result


GOLDEN_REQUEST_DIGEST = (
    "7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e"
)
GOLDEN_RESPONSE_DIGEST = (
    "c46c6db07c702f5d7b4ea45f778883307611d1b5548c3b05dc3f6f5dec4453ab"
)
GOLDEN_REVIEW_BYTES = (
    b'{"artifact":"vocab.t11.human-review","decision":"APPROVE",'
    b'"response_digest":"c46c6db07c702f5d7b4ea45f778883307611d1b5548c3b05dc3f6f5dec4453ab",'
    b'"reviewer_id":"reviewer-a","reviewer_version":1,"v":1}'
)

TASK_CONTENT_BY_CHANNEL = {
    "R": {
        "passage": "Sự khác biệt giữa hai phương án rất subtle.",
        "question": "What does subtle mean in this passage?",
        "learner_response": (
            "It means the difference is slight and not immediately obvious."
        ),
    },
    "L": {
        "spoken_script": "The distinction between the proposals was subtle.",
        "question": "How did the speaker describe the distinction?",
        "learner_response": "The distinction was slight and hard to notice.",
    },
    "W": {
        "production_prompt": "Compare two similar research results.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "learner_response": "The studies showed a subtle difference in timing.",
    },
    "S": {
        "production_prompt": "Describe a small difference between two plans.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "approved_transcript": "There is a subtle difference in their priorities.",
    },
}


def make_request(channel: str = "R") -> dict[str, object]:
    return build_semantic_request(
        unit_key="subtle::small-difference",
        lemma="subtle",
        unit_type="word",
        definition_en="not immediately obvious; tinh tế",
        channel=channel,
        task_content=TASK_CONTENT_BY_CHANNEL[channel],
    )


def make_imported_proposal(
    *,
    channel: str = "R",
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    semantic_rationale: str = (
        "The learner correctly paraphrases the target sense as a slight, "
        "hard-to-notice difference."
    ),
) -> ImportedSemanticProposal:
    request = make_request(channel)
    proposal = {
        "artifact": "vocab.t11.semantic-response",
        "v": 1,
        "request_digest": semantic_request_digest(request),
        "outcome": outcome,
        "failure_code": failure_code,
        "reason_code": reason_code,
        "semantic_rationale": semantic_rationale,
    }
    return import_semantic_response(
        json.dumps(
            proposal,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )


def make_imported_review(
    imported_proposal: ImportedSemanticProposal,
    *,
    decision: str,
) -> ImportedHumanReview:
    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision=decision,
    )
    return import_human_review(
        serialize_human_review(
            review,
            imported_proposal=imported_proposal,
        ),
        imported_proposal=imported_proposal,
    )


APPROVE_CASES = (
    ("R", ASSESSMENT_OUTCOME_PASS, "", ""),
    ("R", ASSESSMENT_OUTCOME_FAIL, "wrong_meaning", ""),
    ("R", ASSESSMENT_OUTCOME_ABSTAIN, "", "semantic_uncertainty"),
    ("W", ASSESSMENT_OUTCOME_FAIL, "semantic_misuse", ""),
)


@pytest.mark.parametrize(
    ("channel", "outcome", "failure_code", "reason_code"),
    APPROVE_CASES,
)
def test_approve_returns_exact_existing_semantic_result_object(
    channel: str,
    outcome: str,
    failure_code: str,
    reason_code: str,
) -> None:
    imported_proposal = make_imported_proposal(
        channel=channel,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
    )
    imported_review = make_imported_review(
        imported_proposal,
        decision="APPROVE",
    )

    materialized = materialize_reviewed_t11_result(
        imported_proposal=imported_proposal,
        imported_review=imported_review,
    )

    assert materialized is imported_proposal.assessment_result
    assert (
        materialized.channel,
        materialized.outcome,
        materialized.failure_code,
        materialized.reason_code,
    ) == (channel, outcome, failure_code, reason_code)
    assert validate_t11_assessment_result(materialized) == ()


REJECT_CASES = (
    ("R", ASSESSMENT_OUTCOME_PASS, "", ""),
    ("R", ASSESSMENT_OUTCOME_FAIL, "wrong_meaning", ""),
    ("R", ASSESSMENT_OUTCOME_ABSTAIN, "", "semantic_uncertainty"),
    ("W", ASSESSMENT_OUTCOME_FAIL, "semantic_misuse", ""),
    ("S", ASSESSMENT_OUTCOME_PASS, "", ""),
)


@pytest.mark.parametrize(
    ("channel", "outcome", "failure_code", "reason_code"),
    REJECT_CASES,
)
def test_reject_constructs_exact_audit_only_abstain_result(
    channel: str,
    outcome: str,
    failure_code: str,
    reason_code: str,
) -> None:
    imported_proposal = make_imported_proposal(
        channel=channel,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
    )
    imported_review = make_imported_review(
        imported_proposal,
        decision="REJECT",
    )

    materialized = materialize_reviewed_t11_result(
        imported_proposal=imported_proposal,
        imported_review=imported_review,
    )

    assert materialized is not imported_proposal.assessment_result
    assert (
        materialized.unit_key,
        materialized.channel,
        materialized.outcome,
        materialized.failure_code,
        materialized.reason_code,
    ) == (
        imported_proposal.assessment_result.unit_key,
        channel,
        ASSESSMENT_OUTCOME_ABSTAIN,
        "",
        "reviewer_rejected",
    )
    assert validate_t11_assessment_result(materialized) == ()


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_review_for_first_proposal_cannot_materialize_second_proposal(
    decision: str,
) -> None:
    first = make_imported_proposal()
    second = make_imported_proposal(
        semantic_rationale=(
            "The learner correctly paraphrases the target sense with altered wording."
        )
    )
    first_review = make_imported_review(first, decision=decision)

    assert first.response_digest != second.response_digest
    with pytest.raises(T11MaterializationError, match="bind"):
        materialize_reviewed_t11_result(
            imported_proposal=second,
            imported_review=first_review,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("response_digest", "0" * 64),
        ("reviewer_id", "reviewer-b"),
        ("reviewer_version", 2),
        ("decision", "REJECT"),
    ],
)
def test_human_review_convenience_field_drift_fails_closed(
    field: str,
    replacement: object,
) -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(
        imported_proposal,
        decision="APPROVE",
    )
    tampered = replace(imported_review, **{field: replacement})

    with pytest.raises(T11MaterializationError, match="runtime fields"):
        materialize_reviewed_t11_result(
            imported_proposal=imported_proposal,
            imported_review=tampered,
        )


def test_semantic_result_outcome_drift_fails_closed() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")
    drifted_result = T11AssessmentResult(
        unit_key=imported_proposal.assessment_result.unit_key,
        channel="R",
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code="semantic_uncertainty",
    )
    tampered = replace(imported_proposal, assessment_result=drifted_result)

    assert validate_t11_assessment_result(drifted_result) == ()
    with pytest.raises(T11MaterializationError, match="disagrees"):
        materialize_reviewed_t11_result(
            imported_proposal=tampered,
            imported_review=imported_review,
        )


def test_semantic_result_failure_code_drift_fails_closed() -> None:
    imported_proposal = make_imported_proposal(
        channel="W",
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code="semantic_misuse",
    )
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")
    drifted_result = replace(
        imported_proposal.assessment_result,
        failure_code="collocation_misuse",
    )
    tampered = replace(imported_proposal, assessment_result=drifted_result)

    assert validate_t11_assessment_result(drifted_result) == ()
    with pytest.raises(T11MaterializationError, match="disagrees"):
        materialize_reviewed_t11_result(
            imported_proposal=tampered,
            imported_review=imported_review,
        )


def test_semantic_result_reason_code_drift_fails_closed() -> None:
    imported_proposal = make_imported_proposal(
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code="semantic_uncertainty",
    )
    imported_review = make_imported_review(imported_proposal, decision="REJECT")
    drifted_result = replace(
        imported_proposal.assessment_result,
        reason_code="off_topic",
    )
    tampered = replace(imported_proposal, assessment_result=drifted_result)

    assert validate_t11_assessment_result(drifted_result) == ()
    with pytest.raises(T11MaterializationError, match="disagrees"):
        materialize_reviewed_t11_result(
            imported_proposal=tampered,
            imported_review=imported_review,
        )


def test_invalid_generic_semantic_result_fails_before_materialization() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")
    invalid_result = replace(
        imported_proposal.assessment_result,
        failure_code="wrong_meaning",
    )
    tampered = replace(imported_proposal, assessment_result=invalid_result)

    assert validate_t11_assessment_result(invalid_result)
    with pytest.raises(T11MaterializationError, match="violates"):
        materialize_reviewed_t11_result(
            imported_proposal=tampered,
            imported_review=imported_review,
        )


def test_wrong_nested_semantic_result_type_fails_closed() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")
    tampered = replace(
        imported_proposal,
        assessment_result="not-a-result",  # type: ignore[arg-type]
    )

    with pytest.raises(T11MaterializationError, match="T11AssessmentResult"):
        materialize_reviewed_t11_result(
            imported_proposal=tampered,
            imported_review=imported_review,
        )


@pytest.mark.parametrize("argument", [None, {}, "runtime", 1])
def test_wrong_imported_proposal_type_is_api_misuse(argument: object) -> None:
    valid_proposal = make_imported_proposal()
    imported_review = make_imported_review(valid_proposal, decision="APPROVE")

    with pytest.raises(TypeError):
        materialize_reviewed_t11_result(
            imported_proposal=argument,  # type: ignore[arg-type]
            imported_review=imported_review,
        )


@pytest.mark.parametrize("argument", [None, {}, "runtime", 1])
def test_wrong_imported_review_type_is_api_misuse(argument: object) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(TypeError):
        materialize_reviewed_t11_result(
            imported_proposal=imported_proposal,
            imported_review=argument,  # type: ignore[arg-type]
        )


def test_materializer_has_no_second_editable_decision_or_result_parameters() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")

    with pytest.raises(TypeError):
        materialize_reviewed_t11_result(  # type: ignore[call-arg]
            imported_proposal=imported_proposal,
            imported_review=imported_review,
            decision="REJECT",
        )


def test_unknown_fabricated_review_decision_never_defaults() -> None:
    imported_proposal = make_imported_proposal()
    valid_review = make_imported_review(imported_proposal, decision="APPROVE")
    changed_mapping = dict(valid_review.review)
    changed_mapping["decision"] = "UNKNOWN"
    fabricated = replace(
        valid_review,
        review=changed_mapping,
        decision="UNKNOWN",
    )

    with pytest.raises(T11MaterializationError):
        materialize_reviewed_t11_result(
            imported_proposal=imported_proposal,
            imported_review=fabricated,
        )


def test_reject_preserves_all_upstream_audit_evidence_exactly() -> None:
    imported_proposal = make_imported_proposal(
        channel="W",
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code="semantic_misuse",
        semantic_rationale="The target collocation is semantically incompatible.",
    )
    imported_review = make_imported_review(imported_proposal, decision="REJECT")
    proposal_before = copy.deepcopy(dict(imported_proposal.proposal))
    rationale_before = imported_proposal.proposal["semantic_rationale"]
    response_digest_before = imported_proposal.response_digest
    assessment_result_before = imported_proposal.assessment_result
    facts_before = copy.deepcopy(dict(imported_proposal.semantic_judge_facts))
    review_before = copy.deepcopy(dict(imported_review.review))

    materialized = materialize_reviewed_t11_result(
        imported_proposal=imported_proposal,
        imported_review=imported_review,
    )

    assert materialized is not assessment_result_before
    assert dict(imported_proposal.proposal) == proposal_before
    assert imported_proposal.proposal["semantic_rationale"] == rationale_before
    assert imported_proposal.response_digest == response_digest_before
    assert imported_proposal.assessment_result is assessment_result_before
    assert dict(imported_proposal.semantic_judge_facts) == facts_before
    assert dict(imported_review.review) == review_before


def test_upstream_golden_request_response_and_review_remain_exact() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="APPROVE")

    assert semantic_request_digest(make_request()) == GOLDEN_REQUEST_DIGEST
    assert imported_proposal.response_digest == GOLDEN_RESPONSE_DIGEST
    assert len(GOLDEN_REVIEW_BYTES) == 197
    assert serialize_human_review(
        imported_review.review,
        imported_proposal=imported_proposal,
    ) == GOLDEN_REVIEW_BYTES


def test_no_materialization_artifact_digest_id_or_provenance_is_created() -> None:
    imported_proposal = make_imported_proposal()
    imported_review = make_imported_review(imported_proposal, decision="REJECT")
    materialized = materialize_reviewed_t11_result(
        imported_proposal=imported_proposal,
        imported_review=imported_review,
    )

    assert not hasattr(materialized, "materialization_id")
    assert not hasattr(materialized, "materialization_digest")
    assert not hasattr(materialized, "review_digest")
    assert not hasattr(materialized, "review_id")
    assert not hasattr(materialized, "provenance")
    assert not hasattr(materialized, "producer")


def test_materialization_module_has_only_allowed_pure_imports() -> None:
    tree = ast.parse(inspect.getsource(materialization_module))
    prohibited = {
        "artifact_json",
        "events",
        "anki",
        "reconcile",
        "session",
        "tts",
        "corpus",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[-1])

    assert imported.isdisjoint(prohibited)
