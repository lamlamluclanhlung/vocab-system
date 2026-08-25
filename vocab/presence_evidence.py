"""Pure D64/D66 target-presence evidence for W and verified S text."""

from __future__ import annotations

from dataclasses import dataclass, field

from .artifact_json import canonical_json_bytes
from .assessment_evidence import (
    ValidatedAttemptEvidence,
    ValidatedUnitEvidence,
    _require_attempt_evidence,
    _require_attempt_unit_binding,
    _require_unit_evidence,
    _unit_binding,
)
from .validators import contains_unit
from .transcription_evidence import (
    TranscriptionEvidence,
    _require_transcription_evidence,
    _transcription_identity,
)


PRESENCE_GATE_ID = "d19-target-presence"
PRESENCE_GATE_VERSION = 1


class PresenceEvidenceError(ValueError):
    """Raised when W/S presence evidence cannot be derived or rebound exactly."""


_PRESENCE_SEAL = object()
_PRESENCE_SNAPSHOT_DOMAIN = "vocab.t12.presence-gate-evidence"
_PRESENCE_SNAPSHOT_VERSION = 1


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
    _transcription_identity: bytes | None = field(repr=False, compare=False)
    _snapshot_bytes: bytes = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> PresenceGateEvidence:
        raise TypeError(
            "PresenceGateEvidence can only be issued by a presence evaluator"
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
    target_present = _evaluate_target_presence(validated_attempt, validated_unit)
    unit_identity = _unit_binding(validated_unit)

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
    object.__setattr__(evidence, "_unit_identity", unit_identity)
    object.__setattr__(evidence, "_transcription_identity", None)
    object.__setattr__(
        evidence,
        "_snapshot_bytes",
        _presence_snapshot_bytes(
            attempt_id=validated_attempt.attempt_id,
            unit_key=validated_unit.unit_key,
            channel="W",
            source_artifact_ref=validated_attempt.response_artifact_ref,
            gate_id=PRESENCE_GATE_ID,
            gate_version=PRESENCE_GATE_VERSION,
            target_present=target_present,
            unit_identity=unit_identity,
            transcription_identity=None,
        ),
    )
    object.__setattr__(evidence, "_seal", _PRESENCE_SEAL)
    return evidence


def evaluate_speech_presence_gate(
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    transcription: TranscriptionEvidence,
) -> PresenceGateEvidence:
    """Evaluate D19 only over one exact human-approved SUCCESS transcript."""
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    _require_attempt_unit_binding(validated_attempt, validated_unit)
    if validated_attempt.channel != "S":
        raise PresenceEvidenceError(
            "speech presence gating requires an S attempt"
        )
    validated_transcription = _require_transcription_evidence(
        transcription,
        attempt=validated_attempt,
    )
    if validated_transcription.status != "SUCCESS":
        raise PresenceEvidenceError(
            "only SUCCESS transcription evidence may enter D19"
        )
    if (
        validated_transcription.response_audio_ref
        != validated_attempt.response_artifact_ref
    ):
        raise PresenceEvidenceError(
            "transcription raw-audio identity does not match the attempt"
        )
    target_present = _evaluate_speech_target_presence(
        validated_transcription,
        validated_unit,
    )
    unit_identity = _unit_binding(validated_unit)
    transcription_identity = _transcription_identity(validated_transcription)

    evidence = object.__new__(PresenceGateEvidence)
    object.__setattr__(evidence, "attempt_id", validated_attempt.attempt_id)
    object.__setattr__(evidence, "unit_key", validated_unit.unit_key)
    object.__setattr__(evidence, "channel", "S")
    object.__setattr__(
        evidence,
        "source_artifact_ref",
        validated_transcription.approved_transcript_ref,
    )
    object.__setattr__(evidence, "gate_id", PRESENCE_GATE_ID)
    object.__setattr__(evidence, "gate_version", PRESENCE_GATE_VERSION)
    object.__setattr__(evidence, "target_present", target_present)
    object.__setattr__(evidence, "_unit_identity", unit_identity)
    object.__setattr__(
        evidence,
        "_transcription_identity",
        transcription_identity,
    )
    object.__setattr__(
        evidence,
        "_snapshot_bytes",
        _presence_snapshot_bytes(
            attempt_id=validated_attempt.attempt_id,
            unit_key=validated_unit.unit_key,
            channel="S",
            source_artifact_ref=validated_transcription.approved_transcript_ref,
            gate_id=PRESENCE_GATE_ID,
            gate_version=PRESENCE_GATE_VERSION,
            target_present=target_present,
            unit_identity=unit_identity,
            transcription_identity=transcription_identity,
        ),
    )
    object.__setattr__(evidence, "_seal", _PRESENCE_SEAL)
    return evidence


def _require_presence_evidence(
    value: object,
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    transcription: TranscriptionEvidence | None = None,
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
    if value.channel not in ("W", "S") or type(value.target_present) is not bool:
        raise PresenceEvidenceError("presence evidence runtime fields are incoherent")
    if validated_attempt.channel == "W":
        if transcription is not None:
            raise PresenceEvidenceError(
                "W presence evidence does not accept transcription evidence"
            )
        expected_target_present = _evaluate_target_presence(
            validated_attempt,
            validated_unit,
        )
        expected_source_ref = validated_attempt.response_artifact_ref
        transcription_identity = None
    elif validated_attempt.channel == "S":
        if transcription is None:
            raise PresenceEvidenceError(
                "S presence evidence requires transcription evidence"
            )
        validated_transcription = _require_transcription_evidence(
            transcription,
            attempt=validated_attempt,
        )
        if validated_transcription.status != "SUCCESS":
            raise PresenceEvidenceError(
                "only SUCCESS transcription evidence may enter D19"
            )
        expected_target_present = _evaluate_speech_target_presence(
            validated_transcription,
            validated_unit,
        )
        expected_source_ref = validated_transcription.approved_transcript_ref
        transcription_identity = _transcription_identity(
            validated_transcription
        )
    else:
        raise PresenceEvidenceError(
            "presence evidence binds only W or S attempts"
        )
    unit_identity = _unit_binding(validated_unit)
    expected_binding = (
        validated_attempt.attempt_id,
        validated_unit.unit_key,
        validated_attempt.channel,
        expected_source_ref,
        PRESENCE_GATE_ID,
        PRESENCE_GATE_VERSION,
        expected_target_present,
        unit_identity,
        transcription_identity,
    )
    actual_binding = (
        value.attempt_id,
        value.unit_key,
        value.channel,
        value.source_artifact_ref,
        value.gate_id,
        value.gate_version,
        value.target_present,
        value._unit_identity,
        value._transcription_identity,
    )
    if actual_binding != expected_binding:
        raise PresenceEvidenceError(
            "presence evidence does not bind this attempt, source, and Unit"
        )
    if type(value._snapshot_bytes) is not bytes:
        raise PresenceEvidenceError("presence evidence issuance snapshot is invalid")
    current_snapshot = _presence_snapshot_bytes(
        attempt_id=value.attempt_id,
        unit_key=value.unit_key,
        channel=value.channel,
        source_artifact_ref=value.source_artifact_ref,
        gate_id=value.gate_id,
        gate_version=value.gate_version,
        target_present=value.target_present,
        unit_identity=value._unit_identity,
        transcription_identity=value._transcription_identity,
    )
    if current_snapshot != value._snapshot_bytes:
        raise PresenceEvidenceError(
            "presence evidence runtime fields disagree with its issuance snapshot"
        )
    return value


def _evaluate_target_presence(
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
) -> bool:
    try:
        captured_text = attempt.response_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise PresenceEvidenceError(
            "captured written response must be strict UTF-8"
        ) from None
    try:
        return contains_unit(captured_text, unit.lemma, unit.unit_type)
    except (TypeError, ValueError) as exc:
        raise PresenceEvidenceError("D19 presence evaluation failed") from exc


def _evaluate_speech_target_presence(
    transcription: TranscriptionEvidence,
    unit: ValidatedUnitEvidence,
) -> bool:
    try:
        return contains_unit(
            transcription.approved_transcript_text,
            unit.lemma,
            unit.unit_type,
        )
    except (TypeError, ValueError) as exc:
        raise PresenceEvidenceError("D19 speech presence evaluation failed") from exc


def _presence_snapshot_bytes(
    *,
    attempt_id: str,
    unit_key: str,
    channel: str,
    source_artifact_ref: str,
    gate_id: str,
    gate_version: int,
    target_present: bool,
    unit_identity: tuple[object, ...],
    transcription_identity: bytes | None,
) -> bytes:
    snapshot: dict[str, object] = {
        "domain": _PRESENCE_SNAPSHOT_DOMAIN,
        "v": _PRESENCE_SNAPSHOT_VERSION,
        "attempt_id": attempt_id,
        "unit_key": unit_key,
        "channel": channel,
        "source_artifact_ref": source_artifact_ref,
        "gate_id": gate_id,
        "gate_version": gate_version,
        "target_present": target_present,
        "unit_identity": unit_identity,
    }
    if transcription_identity is not None:
        if type(transcription_identity) is not bytes:
            raise PresenceEvidenceError(
                "presence transcription identity is invalid"
            )
        snapshot["transcription_identity"] = transcription_identity.hex()
    return canonical_json_bytes(snapshot)
