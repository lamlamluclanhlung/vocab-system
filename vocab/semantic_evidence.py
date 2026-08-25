"""Attempt-bound D63 rebinding of the frozen T11 semantic artifact chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .artifact_json import canonical_json_bytes, strict_json_loads
from .assessment_evidence import (
    ValidatedAttemptEvidence,
    ValidatedUnitEvidence,
    _require_attempt_evidence,
    _require_attempt_unit_binding,
    _require_unit_evidence,
    _unit_binding,
)
from .assessment_identity import cognitive_stimulus_ref
from .human_review import (
    ImportedHumanReview,
    import_human_review,
    serialize_human_review,
)
from .models import T11AssessmentResult
from .presence_evidence import PresenceGateEvidence, _require_presence_evidence
from .review_materialization import materialize_reviewed_t11_result
from .semantic_request import (
    import_semantic_request,
    serialize_semantic_request,
)
from .semantic_response import (
    ImportedSemanticProposal,
    canonical_semantic_proposal_bytes,
    import_semantic_response,
)


class SemanticEvidenceError(ValueError):
    """Raised when T11 artifacts do not bind one exact captured attempt."""


_SEMANTIC_SEAL = object()
_STIMULUS_FIELDS_BY_CHANNEL = {
    "R": ("passage", "question"),
    "L": ("spoken_script", "question"),
    "W": ("production_prompt", "semantic_constraints"),
}


@dataclass(frozen=True, slots=True, init=False)
class T11SemanticEvidenceBundle:
    """One immutable T11 chain independently rebound to one T12 attempt."""

    attempt_id: str
    unit_key: str
    channel: str
    assessment_result: T11AssessmentResult
    assessor_id: str
    assessor_version: str
    reviewer_id: str
    reviewer_version: int
    review_decision: str
    _request_bytes: bytes = field(repr=False, compare=False)
    _proposal_bytes: bytes = field(repr=False, compare=False)
    _review_bytes: bytes = field(repr=False, compare=False)
    _semantic_judge_bytes: bytes = field(repr=False, compare=False)
    _human_review_bytes: bytes = field(repr=False, compare=False)
    _unit_identity: tuple[object, ...] = field(repr=False, compare=False)
    _presence_identity: tuple[object, ...] | None = field(
        repr=False,
        compare=False,
    )
    _seal: object = field(repr=False, compare=False)

    def __new__(
        cls,
        *_args: object,
        **_kwargs: object,
    ) -> T11SemanticEvidenceBundle:
        raise TypeError(
            "T11SemanticEvidenceBundle can only be issued by "
            "bind_t11_semantic_evidence"
        )

    def semantic_judge_provenance(self) -> dict[str, object]:
        """Return a detached exact D57 semantic-judge stage."""
        return _detached_object(self._semantic_judge_bytes, "semantic_judge")

    def human_review_provenance(self) -> dict[str, object]:
        """Return a detached exact D57 human-review stage."""
        return _detached_object(self._human_review_bytes, "human_review")


@dataclass(frozen=True, slots=True)
class _SemanticMaterial:
    request: dict[str, object]
    proposal: ImportedSemanticProposal
    review: ImportedHumanReview
    result: T11AssessmentResult
    request_bytes: bytes
    proposal_bytes: bytes
    review_bytes: bytes
    semantic_judge_bytes: bytes
    human_review_bytes: bytes
    presence_identity: tuple[object, ...] | None


def bind_t11_semantic_evidence(
    *,
    request_raw: bytes,
    proposal_raw: bytes,
    review_raw: bytes,
    assessor_id: str,
    assessor_version: str,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    presence: PresenceGateEvidence | None = None,
) -> T11SemanticEvidenceBundle:
    """Reimport, rebind, review, and materialize one exact T11 artifact chain."""
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    material = _bind_and_materialize(
        request_raw=request_raw,
        proposal_raw=proposal_raw,
        review_raw=review_raw,
        assessor_id=assessor_id,
        assessor_version=assessor_version,
        attempt=validated_attempt,
        unit=validated_unit,
        presence=presence,
    )

    bundle = object.__new__(T11SemanticEvidenceBundle)
    object.__setattr__(bundle, "attempt_id", validated_attempt.attempt_id)
    object.__setattr__(bundle, "unit_key", validated_unit.unit_key)
    object.__setattr__(bundle, "channel", validated_attempt.channel)
    object.__setattr__(bundle, "assessment_result", material.result)
    object.__setattr__(bundle, "assessor_id", material.proposal.assessor_id)
    object.__setattr__(
        bundle,
        "assessor_version",
        material.proposal.assessor_version,
    )
    object.__setattr__(bundle, "reviewer_id", material.review.reviewer_id)
    object.__setattr__(
        bundle,
        "reviewer_version",
        material.review.reviewer_version,
    )
    object.__setattr__(bundle, "review_decision", material.review.decision)
    object.__setattr__(bundle, "_request_bytes", material.request_bytes)
    object.__setattr__(bundle, "_proposal_bytes", material.proposal_bytes)
    object.__setattr__(bundle, "_review_bytes", material.review_bytes)
    object.__setattr__(
        bundle,
        "_semantic_judge_bytes",
        material.semantic_judge_bytes,
    )
    object.__setattr__(
        bundle,
        "_human_review_bytes",
        material.human_review_bytes,
    )
    object.__setattr__(bundle, "_unit_identity", _unit_binding(validated_unit))
    object.__setattr__(
        bundle,
        "_presence_identity",
        material.presence_identity,
    )
    object.__setattr__(bundle, "_seal", _SEMANTIC_SEAL)
    return bundle


def _bind_and_materialize(
    *,
    request_raw: bytes,
    proposal_raw: bytes,
    review_raw: bytes,
    assessor_id: str,
    assessor_version: str,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    presence: PresenceGateEvidence | None,
) -> _SemanticMaterial:
    _require_attempt_unit_binding(attempt, unit)
    if attempt.channel not in _STIMULUS_FIELDS_BY_CHANNEL:
        raise SemanticEvidenceError("T12.2a semantic binding supports only R/L/W")

    presence_identity: tuple[object, ...] | None = None
    if attempt.channel in ("R", "L"):
        if presence is not None:
            raise SemanticEvidenceError("R/L semantic paths do not accept a presence gate")
    else:
        if presence is None:
            raise SemanticEvidenceError(
                "W semantic binding requires target-present presence evidence"
            )
        validated_presence = _require_presence_evidence(
            presence,
            attempt=attempt,
            unit=unit,
        )
        if not validated_presence.target_present:
            raise SemanticEvidenceError(
                "target-absent W evidence forbids semantic artifacts"
            )
        presence_identity = _presence_binding(validated_presence)

    request = import_semantic_request(request_raw)
    request_unit = cast(dict[str, object], request["unit"])
    request_task = cast(dict[str, object], request["task"])
    expected_unit = {
        "unit_key": unit.unit_key,
        "lemma": unit.lemma,
        "unit_type": unit.unit_type,
        "definition_en": unit.definition_en,
    }
    if request_unit != expected_unit:
        raise SemanticEvidenceError(
            "semantic request Unit block does not match validated Unit evidence"
        )
    if request_task["channel"] != attempt.channel:
        raise SemanticEvidenceError(
            "semantic request channel does not match attempt evidence"
        )
    if request_task["task_kind"] != attempt.task_kind:
        raise SemanticEvidenceError(
            "semantic request task_kind does not match attempt evidence"
        )

    stimulus_fields = _STIMULUS_FIELDS_BY_CHANNEL[attempt.channel]
    supplied_stimulus = {
        field_name: request_task[field_name]
        for field_name in stimulus_fields
    }
    supplied_stimulus_ref = cognitive_stimulus_ref(
        unit_key=unit.unit_key,
        channel=attempt.channel,
        task_kind=attempt.task_kind,
        stimulus=supplied_stimulus,
    )
    if supplied_stimulus_ref != attempt.presented_stimulus_ref:
        raise SemanticEvidenceError(
            "semantic request cognitive stimulus does not match the attempt"
        )

    try:
        captured_response = attempt.response_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise SemanticEvidenceError(
            "captured text response must be strict UTF-8"
        ) from None
    if request_task["learner_response"] != captured_response:
        raise SemanticEvidenceError(
            "semantic request learner_response does not exactly match captured bytes"
        )

    proposal = import_semantic_response(
        proposal_raw,
        request=request,
        assessor_id=assessor_id,
        assessor_version=assessor_version,
    )
    review = import_human_review(
        review_raw,
        imported_proposal=proposal,
    )
    result = materialize_reviewed_t11_result(
        imported_proposal=proposal,
        imported_review=review,
    )
    if result.unit_key != attempt.unit_key or result.channel != attempt.channel:
        raise SemanticEvidenceError(
            "materialized T11 result does not match the attempt"
        )

    request_bytes = serialize_semantic_request(request)
    proposal_bytes = canonical_semantic_proposal_bytes(proposal)
    review_bytes = serialize_human_review(
        review.review,
        imported_proposal=proposal,
    )
    semantic_judge_bytes = canonical_json_bytes(dict(proposal.semantic_judge_facts))
    human_review_bytes = canonical_json_bytes(
        {
            "reviewer_id": review.reviewer_id,
            "reviewer_version": review.reviewer_version,
            "decision": review.decision,
        }
    )
    return _SemanticMaterial(
        request=request,
        proposal=proposal,
        review=review,
        result=result,
        request_bytes=request_bytes,
        proposal_bytes=proposal_bytes,
        review_bytes=review_bytes,
        semantic_judge_bytes=semantic_judge_bytes,
        human_review_bytes=human_review_bytes,
        presence_identity=presence_identity,
    )


def _require_semantic_evidence(
    value: object,
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    presence: PresenceGateEvidence | None,
) -> tuple[T11SemanticEvidenceBundle, _SemanticMaterial]:
    if type(value) is not T11SemanticEvidenceBundle:
        raise TypeError("semantic must be a T11SemanticEvidenceBundle")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError(
            "semantic evidence was not issued by bind_t11_semantic_evidence"
        ) from None
    if seal is not _SEMANTIC_SEAL:
        raise TypeError(
            "semantic evidence was not issued by bind_t11_semantic_evidence"
        )

    material = _bind_and_materialize(
        request_raw=value._request_bytes,
        proposal_raw=value._proposal_bytes,
        review_raw=value._review_bytes,
        assessor_id=value.assessor_id,
        assessor_version=value.assessor_version,
        attempt=attempt,
        unit=unit,
        presence=presence,
    )
    expected_runtime = (
        attempt.attempt_id,
        unit.unit_key,
        attempt.channel,
        material.result,
        material.proposal.assessor_id,
        material.proposal.assessor_version,
        material.review.reviewer_id,
        material.review.reviewer_version,
        material.review.decision,
        material.request_bytes,
        material.proposal_bytes,
        material.review_bytes,
        material.semantic_judge_bytes,
        material.human_review_bytes,
        _unit_binding(unit),
        material.presence_identity,
    )
    actual_runtime = (
        value.attempt_id,
        value.unit_key,
        value.channel,
        value.assessment_result,
        value.assessor_id,
        value.assessor_version,
        value.reviewer_id,
        value.reviewer_version,
        value.review_decision,
        value._request_bytes,
        value._proposal_bytes,
        value._review_bytes,
        value._semantic_judge_bytes,
        value._human_review_bytes,
        value._unit_identity,
        value._presence_identity,
    )
    if actual_runtime != expected_runtime:
        raise SemanticEvidenceError(
            "semantic bundle runtime fields disagree with independent rebinding"
        )
    return value, material


def _presence_binding(value: PresenceGateEvidence) -> tuple[object, ...]:
    return (
        value.attempt_id,
        value.unit_key,
        value.channel,
        value.source_artifact_ref,
        value.gate_id,
        value.gate_version,
        value.target_present,
        value._unit_identity,
    )


def _detached_object(raw: bytes, name: str) -> dict[str, object]:
    value = strict_json_loads(raw)
    if type(value) is not dict:  # pragma: no cover - constructed canonically above
        raise AssertionError(f"{name} provenance is not an object")
    return value
