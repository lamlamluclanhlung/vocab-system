"""Pure D64 target-presence evidence for captured written responses."""

from __future__ import annotations

from dataclasses import dataclass, field

from .assessment_evidence import (
    ValidatedAttemptEvidence,
    ValidatedUnitEvidence,
    _require_attempt_evidence,
    _require_attempt_unit_binding,
    _require_unit_evidence,
    _unit_binding,
)
from .validators import contains_unit


PRESENCE_GATE_ID = "d19-target-presence"
PRESENCE_GATE_VERSION = 1


class PresenceEvidenceError(ValueError):
    """Raised when W presence evidence cannot be derived or rebound exactly."""


_PRESENCE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class PresenceGateEvidence:
    """One sealed attempt/source-scoped result from the frozen D19 matcher."""

    attempt_id: str
    unit_key: str
    channel: str
    source_artifact_ref: str
    gate_id: str
    gate_version: int
    target_present: bool
    _unit_identity: tuple[object, ...] = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> PresenceGateEvidence:
        raise TypeError(
            "PresenceGateEvidence can only be issued by evaluate_presence_gate"
        )

    def to_provenance(self) -> dict[str, object]:
        """Return the exact detached D57 presence-gate stage."""
        return {
            "gate_id": self.gate_id,
            "gate_version": self.gate_version,
            "target_present": self.target_present,
        }


def evaluate_presence_gate(
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
) -> PresenceGateEvidence:
    """Strictly decode captured W text and evaluate D19 exactly once."""
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    _require_attempt_unit_binding(validated_attempt, validated_unit)
    if validated_attempt.channel != "W":
        raise PresenceEvidenceError("presence gating is supported only for W")
    try:
        captured_text = validated_attempt.response_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise PresenceEvidenceError(
            "captured written response must be strict UTF-8"
        ) from None
    try:
        target_present = contains_unit(
            captured_text,
            validated_unit.lemma,
            validated_unit.unit_type,
        )
    except (TypeError, ValueError) as exc:
        raise PresenceEvidenceError("D19 presence evaluation failed") from exc

    evidence = object.__new__(PresenceGateEvidence)
    object.__setattr__(evidence, "attempt_id", validated_attempt.attempt_id)
    object.__setattr__(evidence, "unit_key", validated_unit.unit_key)
    object.__setattr__(evidence, "channel", "W")
    object.__setattr__(
        evidence,
        "source_artifact_ref",
        validated_attempt.response_artifact_ref,
    )
    object.__setattr__(evidence, "gate_id", PRESENCE_GATE_ID)
    object.__setattr__(evidence, "gate_version", PRESENCE_GATE_VERSION)
    object.__setattr__(evidence, "target_present", target_present)
    object.__setattr__(evidence, "_unit_identity", _unit_binding(validated_unit))
    object.__setattr__(evidence, "_seal", _PRESENCE_SEAL)
    return evidence


def _require_presence_evidence(
    value: object,
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
) -> PresenceGateEvidence:
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    if type(value) is not PresenceGateEvidence:
        raise TypeError("presence must be a PresenceGateEvidence")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError(
            "presence evidence was not issued by evaluate_presence_gate"
        ) from None
    if seal is not _PRESENCE_SEAL:
        raise TypeError("presence evidence was not issued by evaluate_presence_gate")
    expected_binding = (
        validated_attempt.attempt_id,
        validated_unit.unit_key,
        validated_attempt.channel,
        validated_attempt.response_artifact_ref,
        PRESENCE_GATE_ID,
        PRESENCE_GATE_VERSION,
        _unit_binding(validated_unit),
    )
    actual_binding = (
        value.attempt_id,
        value.unit_key,
        value.channel,
        value.source_artifact_ref,
        value.gate_id,
        value.gate_version,
        value._unit_identity,
    )
    if actual_binding != expected_binding:
        raise PresenceEvidenceError(
            "presence evidence does not bind this attempt, source, and Unit"
        )
    if value.channel != "W" or type(value.target_present) is not bool:
        raise PresenceEvidenceError("presence evidence runtime fields are incoherent")
    return value
