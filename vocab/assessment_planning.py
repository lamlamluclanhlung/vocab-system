"""Pure D64 R/L/W JUDGE planning from sealed immutable evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .artifact_json import canonical_json_bytes, strict_json_loads
from .assessment_evidence import (
    ValidatedAttemptEvidence,
    ValidatedUnitEvidence,
    _require_attempt_evidence,
    _require_attempt_unit_binding,
    _require_unit_evidence,
)
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_FAILURE_CODES_BY_CHANNEL,
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    HUMAN_REVIEW_DECISIONS,
    SLUG_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)
from .presence_evidence import (
    PRESENCE_GATE_ID,
    PRESENCE_GATE_VERSION,
    PresenceGateEvidence,
    _require_presence_evidence,
)
from .semantic_evidence import (
    T11SemanticEvidenceBundle,
    _require_semantic_evidence,
)
from .semantic_request import (
    SEMANTIC_PROMPT_ID,
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_PROTOCOL_ID,
    SEMANTIC_PROTOCOL_VERSION,
    SEMANTIC_RUBRIC_ID,
    SEMANTIC_RUBRIC_VERSION,
)
from .semantic_response import SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES


POLICY_ID = "t12-assessment-policy"
POLICY_VERSION = 1


class AssessmentPlanningError(ValueError):
    """Raised when sealed evidence cannot produce one exact T12.2a payload."""


_PLANNED_SEAL = object()
_PLANNED_SNAPSHOT_DOMAIN = "vocab.t12.planned-judge"
_PLANNED_SNAPSHOT_VERSION = 1
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_STIMULUS_REF_RE = re.compile(ASSESSMENT_STIMULUS_REF_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID_RE = re.compile(SLUG_PATTERN)

_COMMON_FIELDS = frozenset(
    (
        "channel",
        "passed",
        "model_id",
        "model_version",
        "producer",
        "producer_version",
        "attempt_id",
        "presented_stimulus_ref",
        "outcome",
        "authority_kind",
        "provenance",
        "response_artifact_ref",
    )
)
_D35_FIELDS = frozenset(("assessment_id", "stimulus_ref", "novel"))
_SEMANTIC_JUDGE_FIELDS = frozenset(
    (
        "protocol_id",
        "protocol_version",
        "assessor_id",
        "assessor_version",
        "rubric_id",
        "rubric_version",
        "prompt_id",
        "prompt_version",
        "request_digest",
        "response_digest",
    )
)
_HUMAN_REVIEW_FIELDS = frozenset(
    ("reviewer_id", "reviewer_version", "decision")
)
_PRESENCE_GATE_FIELDS = frozenset(
    ("gate_id", "gate_version", "target_present")
)
_POLICY_FIELDS = frozenset(("policy_id", "policy_version"))


@dataclass(frozen=True, slots=True, init=False)
class PlannedJudge:
    """A non-Event output owning one canonical deeply immutable JUDGE payload."""

    unit_key: str
    _canonical_payload_bytes: bytes = field(repr=False, compare=True)
    _snapshot_bytes: bytes = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> PlannedJudge:
        raise TypeError("PlannedJudge can only be issued by plan_text_judge")

    @property
    def canonical_payload_bytes(self) -> bytes:
        """Return the immutable canonical JSON representation of the payload."""
        _require_planned_judge(self)
        return self._canonical_payload_bytes

    def to_payload(self) -> dict[str, object]:
        """Return a detached mutable payload copy."""
        return _require_planned_judge(self)

    def to_dict(self) -> dict[str, object]:
        """Alias for the detached payload view."""
        return self.to_payload()


def plan_text_judge(
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    semantic: T11SemanticEvidenceBundle | None = None,
    presence: PresenceGateEvidence | None = None,
) -> PlannedJudge:
    """Plan exactly one captured-text R/L/W JUDGE with no I/O or mutation."""
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    _require_attempt_unit_binding(validated_attempt, validated_unit)
    channel = validated_attempt.channel
    if channel not in ("R", "L", "W"):
        raise AssessmentPlanningError("T12.2a planning supports only R/L/W")

    provenance: dict[str, object]
    if channel in ("R", "L"):
        if presence is not None:
            raise AssessmentPlanningError("R/L planning does not accept presence evidence")
        if semantic is None:
            raise AssessmentPlanningError("R/L planning requires semantic evidence")
        _, material = _require_semantic_evidence(
            semantic,
            attempt=validated_attempt,
            unit=validated_unit,
            presence=None,
        )
        result = material.result
        provenance = {
            "semantic_judge": dict(material.proposal.semantic_judge_facts),
            "human_review": {
                "reviewer_id": material.review.reviewer_id,
                "reviewer_version": material.review.reviewer_version,
                "decision": material.review.decision,
            },
        }
    else:
        if presence is None:
            raise AssessmentPlanningError("W planning requires presence evidence")
        validated_presence = _require_presence_evidence(
            presence,
            attempt=validated_attempt,
            unit=validated_unit,
        )
        provenance = {"presence_gate": validated_presence.to_provenance()}
        if not validated_presence.target_present:
            if semantic is not None:
                raise AssessmentPlanningError(
                    "target-absent W planning forbids semantic evidence"
                )
            payload = _base_payload(
                attempt=validated_attempt,
                outcome=ASSESSMENT_OUTCOME_OMITTED,
                authority_kind="deterministic_gate",
                model_id=PRESENCE_GATE_ID,
                model_version=str(PRESENCE_GATE_VERSION),
                provenance=provenance,
            )
            payload["reason_code"] = "target_absent"
            return _issue_planned_judge(validated_unit.unit_key, payload)
        if semantic is None:
            raise AssessmentPlanningError(
                "target-present W planning requires semantic evidence"
            )
        _, material = _require_semantic_evidence(
            semantic,
            attempt=validated_attempt,
            unit=validated_unit,
            presence=validated_presence,
        )
        result = material.result
        provenance.update(
            {
                "semantic_judge": dict(material.proposal.semantic_judge_facts),
                "human_review": {
                    "reviewer_id": material.review.reviewer_id,
                    "reviewer_version": material.review.reviewer_version,
                    "decision": material.review.decision,
                },
            }
        )

    if result.outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        authority_kind = "semantic_model"
        model_id = material.proposal.assessor_id
        model_version = material.proposal.assessor_version
    elif result.outcome == ASSESSMENT_OUTCOME_ABSTAIN:
        authority_kind = "policy"
        model_id = POLICY_ID
        model_version = str(POLICY_VERSION)
        provenance["policy"] = {
            "policy_id": POLICY_ID,
            "policy_version": POLICY_VERSION,
        }
    else:  # pragma: no cover - T11 import/materialization closes this first
        raise AssessmentPlanningError("semantic result outcome is unsupported")

    payload = _base_payload(
        attempt=validated_attempt,
        outcome=result.outcome,
        authority_kind=authority_kind,
        model_id=model_id,
        model_version=model_version,
        provenance=provenance,
    )
    if result.outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        payload.update(
            {
                "assessment_id": validated_attempt.attempt_id,
                "stimulus_ref": validated_attempt.presented_stimulus_ref,
                "novel": validated_attempt.novel,
            }
        )
        if result.outcome == ASSESSMENT_OUTCOME_FAIL:
            payload["failure_code"] = result.failure_code
    else:
        payload["reason_code"] = result.reason_code
    return _issue_planned_judge(validated_unit.unit_key, payload)


def _base_payload(
    *,
    attempt: ValidatedAttemptEvidence,
    outcome: str,
    authority_kind: str,
    model_id: str,
    model_version: str,
    provenance: dict[str, object],
) -> dict[str, object]:
    return {
        "channel": attempt.channel,
        "passed": outcome == ASSESSMENT_OUTCOME_PASS,
        "model_id": model_id,
        "model_version": model_version,
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "attempt_id": attempt.attempt_id,
        "presented_stimulus_ref": attempt.presented_stimulus_ref,
        "outcome": outcome,
        "authority_kind": authority_kind,
        "provenance": provenance,
        "response_artifact_ref": attempt.response_artifact_ref,
    }


def _issue_planned_judge(unit_key: str, payload: object) -> PlannedJudge:
    validated_payload = _validated_judge_payload(unit_key=unit_key, payload=payload)
    canonical_payload_bytes = canonical_json_bytes(validated_payload)
    planned = object.__new__(PlannedJudge)
    object.__setattr__(planned, "unit_key", unit_key)
    object.__setattr__(
        planned,
        "_canonical_payload_bytes",
        canonical_payload_bytes,
    )
    object.__setattr__(
        planned,
        "_snapshot_bytes",
        _planned_snapshot_bytes(
            unit_key=unit_key,
            canonical_payload_bytes=canonical_payload_bytes,
        ),
    )
    object.__setattr__(planned, "_seal", _PLANNED_SEAL)
    return planned


def _require_planned_judge(value: object) -> dict[str, object]:
    if type(value) is not PlannedJudge:
        raise TypeError("planned must be a PlannedJudge")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError("planned JUDGE was not issued by plan_text_judge") from None
    if seal is not _PLANNED_SEAL:
        raise TypeError("planned JUDGE was not issued by plan_text_judge")
    if type(value._canonical_payload_bytes) is not bytes:
        raise AssessmentPlanningError(
            "planned JUDGE canonical payload bytes are invalid"
        )
    try:
        decoded_payload = strict_json_loads(value._canonical_payload_bytes)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AssessmentPlanningError(
            "planned JUDGE canonical payload bytes are invalid"
        ) from exc
    if type(decoded_payload) is not dict:
        raise AssessmentPlanningError("planned JUDGE payload is not an object")
    validated_payload = _validated_judge_payload(
        unit_key=value.unit_key,
        payload=decoded_payload,
    )
    canonical_payload_bytes = canonical_json_bytes(validated_payload)
    if canonical_payload_bytes != value._canonical_payload_bytes:
        raise AssessmentPlanningError(
            "planned JUDGE payload bytes are not exact canonical JSON"
        )
    if type(value._snapshot_bytes) is not bytes:
        raise AssessmentPlanningError("planned JUDGE issuance snapshot is invalid")
    current_snapshot = _planned_snapshot_bytes(
        unit_key=value.unit_key,
        canonical_payload_bytes=value._canonical_payload_bytes,
    )
    if current_snapshot != value._snapshot_bytes:
        raise AssessmentPlanningError(
            "planned JUDGE runtime fields disagree with its issuance snapshot"
        )
    return validated_payload


def _planned_snapshot_bytes(
    *,
    unit_key: str,
    canonical_payload_bytes: bytes,
) -> bytes:
    return canonical_json_bytes(
        {
            "domain": _PLANNED_SNAPSHOT_DOMAIN,
            "v": _PLANNED_SNAPSHOT_VERSION,
            "unit_key": unit_key,
            "payload_sha256": hashlib.sha256(canonical_payload_bytes).hexdigest(),
        }
    )


def _validated_judge_payload(
    *,
    unit_key: object,
    payload: object,
) -> dict[str, object]:
    if type(unit_key) is not str or _UNIT_KEY_RE.fullmatch(unit_key) is None:
        raise AssessmentPlanningError("planned JUDGE unit_key is invalid")
    if not isinstance(payload, Mapping):
        raise AssessmentPlanningError("planned JUDGE payload must be an object")

    outcome = payload.get("outcome")
    expected_fields: frozenset[str]
    if outcome == ASSESSMENT_OUTCOME_PASS:
        expected_fields = _COMMON_FIELDS | _D35_FIELDS
    elif outcome == ASSESSMENT_OUTCOME_FAIL:
        expected_fields = _COMMON_FIELDS | _D35_FIELDS | {"failure_code"}
    elif outcome in (ASSESSMENT_OUTCOME_OMITTED, ASSESSMENT_OUTCOME_ABSTAIN):
        expected_fields = _COMMON_FIELDS | {"reason_code"}
    else:
        raise AssessmentPlanningError("planned JUDGE outcome is unsupported")
    if set(payload) != expected_fields:
        raise AssessmentPlanningError("planned JUDGE payload has the wrong key set")

    channel = payload["channel"]
    if type(channel) is not str or channel not in ("R", "L", "W"):
        raise AssessmentPlanningError("planned JUDGE channel is unsupported")
    if type(payload["passed"]) is not bool or payload["passed"] is not (
        outcome == ASSESSMENT_OUTCOME_PASS
    ):
        raise AssessmentPlanningError("planned JUDGE passed/outcome invariant failed")
    if payload["producer"] != T12_ASSESSMENT_PRODUCER_ID:
        raise AssessmentPlanningError("planned JUDGE producer is invalid")
    if (
        type(payload["producer_version"]) is not int
        or payload["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise AssessmentPlanningError("planned JUDGE producer_version is invalid")
    _require_pattern(payload["attempt_id"], _ATTEMPT_ID_RE, "attempt_id")
    _require_pattern(
        payload["presented_stimulus_ref"],
        _STIMULUS_REF_RE,
        "presented_stimulus_ref",
    )
    _require_pattern(
        payload["response_artifact_ref"],
        _ARTIFACT_REF_RE,
        "response_artifact_ref",
    )
    if type(payload["model_id"]) is not str or not payload["model_id"]:
        raise AssessmentPlanningError("planned JUDGE model_id is invalid")
    if type(payload["model_version"]) is not str or not payload["model_version"]:
        raise AssessmentPlanningError("planned JUDGE model_version is invalid")

    provenance = payload["provenance"]
    if not isinstance(provenance, Mapping):
        raise AssessmentPlanningError("planned JUDGE provenance must be an object")
    expected_stages: set[str]
    if outcome == ASSESSMENT_OUTCOME_OMITTED:
        if channel != "W" or payload["reason_code"] != "target_absent":
            raise AssessmentPlanningError("T12.2a OMITTED closure is invalid")
        expected_stages = {"presence_gate"}
        _require_authority(
            payload,
            kind="deterministic_gate",
            model_id=PRESENCE_GATE_ID,
            model_version=str(PRESENCE_GATE_VERSION),
        )
    else:
        expected_stages = {"semantic_judge", "human_review"}
        if channel == "W":
            expected_stages.add("presence_gate")
        if outcome == ASSESSMENT_OUTCOME_ABSTAIN:
            reason_code = payload["reason_code"]
            if reason_code not in (
                *SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES,
                "reviewer_rejected",
            ):
                raise AssessmentPlanningError(
                    "T12.2a semantic ABSTAIN reason is unsupported"
                )
            expected_stages.add("policy")
            _require_authority(
                payload,
                kind="policy",
                model_id=POLICY_ID,
                model_version=str(POLICY_VERSION),
            )
        else:
            _require_authority(
                payload,
                kind="semantic_model",
                model_id=None,
                model_version=None,
            )
    if set(provenance) != expected_stages:
        raise AssessmentPlanningError("planned JUDGE provenance has wrong stages")

    semantic_stage: Mapping[object, object] | None = None
    if "semantic_judge" in provenance:
        semantic_stage = _validated_semantic_stage(provenance["semantic_judge"])
    review_stage: Mapping[object, object] | None = None
    if "human_review" in provenance:
        review_stage = _validated_review_stage(provenance["human_review"])
    if "presence_gate" in provenance:
        target_present = _validated_presence_stage(provenance["presence_gate"])
        if target_present is not (outcome != ASSESSMENT_OUTCOME_OMITTED):
            raise AssessmentPlanningError(
                "presence-gate target_present disagrees with the outcome path"
            )
    if "policy" in provenance:
        _validated_policy_stage(provenance["policy"])

    if semantic_stage is not None and outcome in (
        ASSESSMENT_OUTCOME_PASS,
        ASSESSMENT_OUTCOME_FAIL,
    ):
        if (
            payload["model_id"] != semantic_stage["assessor_id"]
            or payload["model_version"] != semantic_stage["assessor_version"]
        ):
            raise AssessmentPlanningError(
                "semantic top-level authority disagrees with semantic provenance"
            )
    if review_stage is not None:
        expected_decision = (
            "REJECT"
            if outcome == ASSESSMENT_OUTCOME_ABSTAIN
            and payload["reason_code"] == "reviewer_rejected"
            else "APPROVE"
        )
        if review_stage["decision"] != expected_decision:
            raise AssessmentPlanningError(
                "human-review decision disagrees with the final outcome"
            )

    if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        if payload["assessment_id"] != payload["attempt_id"]:
            raise AssessmentPlanningError("assessment_id must equal attempt_id")
        if payload["stimulus_ref"] != payload["presented_stimulus_ref"]:
            raise AssessmentPlanningError(
                "stimulus_ref must equal presented_stimulus_ref"
            )
        if type(payload["novel"]) is not bool:
            raise AssessmentPlanningError("novel must be an actual Boolean")
        if outcome == ASSESSMENT_OUTCOME_FAIL:
            failure_code = payload["failure_code"]
            if (
                type(failure_code) is not str
                or failure_code not in ASSESSMENT_FAILURE_CODES_BY_CHANNEL[channel]
            ):
                raise AssessmentPlanningError(
                    "failure_code is invalid for the planned channel"
                )

    return _detached_payload(payload)


def _validated_semantic_stage(value: object) -> Mapping[object, object]:
    stage = _require_stage(value, _SEMANTIC_JUDGE_FIELDS, "semantic_judge")
    frozen_values = {
        "protocol_id": SEMANTIC_PROTOCOL_ID,
        "protocol_version": SEMANTIC_PROTOCOL_VERSION,
        "rubric_id": SEMANTIC_RUBRIC_ID,
        "rubric_version": SEMANTIC_RUBRIC_VERSION,
        "prompt_id": SEMANTIC_PROMPT_ID,
        "prompt_version": SEMANTIC_PROMPT_VERSION,
    }
    for name, expected in frozen_values.items():
        if stage[name] != expected or type(stage[name]) is not type(expected):
            raise AssessmentPlanningError(f"semantic_judge.{name} is invalid")
    for name in ("assessor_id", "assessor_version"):
        if type(stage[name]) is not str or not stage[name]:
            raise AssessmentPlanningError(f"semantic_judge.{name} is invalid")
    for name in ("request_digest", "response_digest"):
        if type(stage[name]) is not str or _LOWER_SHA256_RE.fullmatch(stage[name]) is None:
            raise AssessmentPlanningError(f"semantic_judge.{name} is invalid")
    return stage


def _validated_review_stage(value: object) -> Mapping[object, object]:
    stage = _require_stage(value, _HUMAN_REVIEW_FIELDS, "human_review")
    if (
        type(stage["reviewer_id"]) is not str
        or _REVIEWER_ID_RE.fullmatch(stage["reviewer_id"]) is None
    ):
        raise AssessmentPlanningError("human_review.reviewer_id is invalid")
    if type(stage["reviewer_version"]) is not int or stage["reviewer_version"] < 1:
        raise AssessmentPlanningError("human_review.reviewer_version is invalid")
    if stage["decision"] not in HUMAN_REVIEW_DECISIONS:
        raise AssessmentPlanningError("human_review.decision is invalid")
    return stage


def _validated_presence_stage(value: object) -> bool:
    stage = _require_stage(value, _PRESENCE_GATE_FIELDS, "presence_gate")
    if stage["gate_id"] != PRESENCE_GATE_ID:
        raise AssessmentPlanningError("presence_gate.gate_id is invalid")
    if (
        type(stage["gate_version"]) is not int
        or stage["gate_version"] != PRESENCE_GATE_VERSION
    ):
        raise AssessmentPlanningError("presence_gate.gate_version is invalid")
    if type(stage["target_present"]) is not bool:
        raise AssessmentPlanningError("presence_gate.target_present is invalid")
    return stage["target_present"]


def _validated_policy_stage(value: object) -> None:
    stage = _require_stage(value, _POLICY_FIELDS, "policy")
    if stage["policy_id"] != POLICY_ID:
        raise AssessmentPlanningError("policy.policy_id is invalid")
    if type(stage["policy_version"]) is not int or stage["policy_version"] != POLICY_VERSION:
        raise AssessmentPlanningError("policy.policy_version is invalid")


def _require_stage(
    value: object,
    fields: frozenset[str],
    name: str,
) -> Mapping[object, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AssessmentPlanningError(f"{name} provenance has the wrong key set")
    return value


def _require_authority(
    payload: Mapping[object, object],
    *,
    kind: str,
    model_id: str | None,
    model_version: str | None,
) -> None:
    if payload["authority_kind"] != kind:
        raise AssessmentPlanningError("planned JUDGE authority_kind is invalid")
    if model_id is not None and payload["model_id"] != model_id:
        raise AssessmentPlanningError("planned JUDGE model_id is invalid")
    if model_version is not None and payload["model_version"] != model_version:
        raise AssessmentPlanningError("planned JUDGE model_version is invalid")


def _require_pattern(value: object, pattern: re.Pattern[str], name: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise AssessmentPlanningError(f"planned JUDGE {name} is invalid")
    return value


def _detached_payload(payload: Mapping[object, object]) -> dict[str, object]:
    try:
        canonical = canonical_json_bytes(payload)
        detached = strict_json_loads(canonical)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AssessmentPlanningError(
            "planned JUDGE payload is not canonical-JSON serializable"
        ) from exc
    if type(detached) is not dict:  # pragma: no cover - mapping input guarantees it
        raise AssertionError("planned JUDGE payload did not serialize as an object")
    return detached
