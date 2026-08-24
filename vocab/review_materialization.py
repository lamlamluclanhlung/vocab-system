"""Pure T11 APPROVE/REJECT materialization for validated runtime objects."""

from __future__ import annotations

from .contracts import ASSESSMENT_OUTCOME_ABSTAIN, HUMAN_REVIEW_DECISIONS
from .human_review import (
    HumanReviewError,
    ImportedHumanReview,
    serialize_human_review,
)
from .models import T11AssessmentResult
from .semantic_response import ImportedSemanticProposal
from .validators import validate_t11_assessment_result


_DECISION_APPROVE, _DECISION_REJECT = HUMAN_REVIEW_DECISIONS


class T11MaterializationError(ValueError):
    """Raised when validated T11 runtime objects are incoherent."""


def materialize_reviewed_t11_result(
    *,
    imported_proposal: ImportedSemanticProposal,
    imported_review: ImportedHumanReview,
) -> T11AssessmentResult:
    """Materialize one reviewed result without persistence or mutation."""
    if not isinstance(imported_proposal, ImportedSemanticProposal):
        raise TypeError("imported_proposal must be an ImportedSemanticProposal")
    if not isinstance(imported_review, ImportedHumanReview):
        raise TypeError("imported_review must be an ImportedHumanReview")

    try:
        serialize_human_review(
            imported_review.review,
            imported_proposal=imported_proposal,
        )
    except HumanReviewError as exc:
        raise T11MaterializationError(str(exc)) from None
    _require_review_wrapper_coherence(imported_review)
    semantic_result = _require_semantic_result_coherence(imported_proposal)

    if imported_review.decision == _DECISION_APPROVE:
        return semantic_result

    if imported_review.decision == _DECISION_REJECT:
        rejected_result = T11AssessmentResult(
            unit_key=semantic_result.unit_key,
            channel=semantic_result.channel,
            outcome=ASSESSMENT_OUTCOME_ABSTAIN,
            failure_code="",
            reason_code="reviewer_rejected",
        )
        violations = validate_t11_assessment_result(rejected_result)
        if violations:
            raise T11MaterializationError(
                "rejected result violates the T11 assessment contract: "
                + ", ".join(violations)
            )
        return rejected_result

    raise T11MaterializationError("review decision is unreachable")


def _require_review_wrapper_coherence(
    imported_review: ImportedHumanReview,
) -> None:
    review = imported_review.review
    expected = (
        review["response_digest"],
        review["reviewer_id"],
        review["reviewer_version"],
        review["decision"],
    )
    actual = (
        imported_review.response_digest,
        imported_review.reviewer_id,
        imported_review.reviewer_version,
        imported_review.decision,
    )
    if actual != expected:
        raise T11MaterializationError(
            "human-review runtime fields disagree with the review artifact"
        )


def _require_semantic_result_coherence(
    imported_proposal: ImportedSemanticProposal,
) -> T11AssessmentResult:
    result = imported_proposal.assessment_result
    if not isinstance(result, T11AssessmentResult):
        raise T11MaterializationError(
            "assessment_result must be a T11AssessmentResult"
        )

    violations = validate_t11_assessment_result(result)
    if violations:
        raise T11MaterializationError(
            "semantic result violates the T11 assessment contract: "
            + ", ".join(violations)
        )

    proposal = imported_proposal.proposal
    semantic_fields = (
        proposal["outcome"],
        proposal["failure_code"],
        proposal["reason_code"],
    )
    result_fields = (
        result.outcome,
        result.failure_code,
        result.reason_code,
    )
    if result_fields != semantic_fields:
        raise T11MaterializationError(
            "assessment_result disagrees with the semantic proposal"
        )
    return result
