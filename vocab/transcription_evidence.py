"""Sealed D65 transcription evidence reconstructed from durable authorities."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .artifact_json import canonical_json_bytes, strict_json_loads
from .artifact_store import ArtifactStore
from .assessment_evidence import ValidatedAttemptEvidence, _require_attempt_evidence
from .transcription_ledger import (
    TranscriptionReceipt,
    _decode_transcription_record,
    validate_transcription_ledger,
)


class TranscriptionEvidenceError(ValueError):
    """Raised when durable transcription authority or its sealed view drifts."""


_TRANSCRIPTION_SEAL = object()
_TRANSCRIPTION_SNAPSHOT_DOMAIN = "vocab.t12.transcription-evidence"
_TRANSCRIPTION_SNAPSHOT_VERSION = 1


@dataclass(frozen=True, slots=True, init=False)
class TranscriptionEvidence:
    """One immutable attempt/audio-bound terminal transcription disposition."""

    attempt_id: str
    response_audio_ref: str
    status: str
    stt_model_id: str
    stt_model_version: str
    decoder_version: str
    stt_output_ref: str
    approved_transcript_ref: str
    approved_transcript_text: str
    verifier_id: str
    verifier_version: int
    uncertainty_code: str
    failure_code: str
    _receipt_bytes: bytes = field(repr=False, compare=False)
    _approved_transcript_bytes: bytes = field(repr=False, compare=False)
    _snapshot_bytes: bytes = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> TranscriptionEvidence:
        raise TypeError(
            "TranscriptionEvidence can only be issued by "
            "load_transcription_evidence"
        )

    def to_provenance(self) -> dict[str, object]:
        """Return the exact detached D57 transcription union."""
        validated = _require_transcription_evidence(self)
        return dict(_receipt_from_evidence(validated).transcription)


def load_transcription_evidence(
    *,
    transcription_path: str | os.PathLike[str],
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    attempt: ValidatedAttemptEvidence,
) -> TranscriptionEvidence:
    """Issue evidence only from complete D55/D60/D65 durable authority."""
    validated_attempt = _require_attempt_evidence(attempt)
    if validated_attempt.channel != "S":
        raise TranscriptionEvidenceError(
            "transcription evidence requires a captured S attempt"
        )
    receipts = validate_transcription_ledger(
        transcription_path,
        exposure_path=exposure_path,
        capture_path=capture_path,
        artifact_store=artifact_store,
    )
    matches = [
        receipt
        for receipt in receipts
        if receipt.attempt_id == validated_attempt.attempt_id
    ]
    if len(matches) != 1:
        raise TranscriptionEvidenceError(
            "transcription evidence requires exactly one durable receipt"
        )
    receipt = matches[0]
    if receipt.response_audio_ref != validated_attempt.response_artifact_ref:
        raise TranscriptionEvidenceError(
            "transcription receipt does not bind the attempt raw audio"
        )

    stage = receipt.transcription
    approved_bytes = b""
    approved_text = ""
    if stage["status"] == "SUCCESS":
        approved_bytes = artifact_store.read(stage["approved_transcript_ref"])
        try:
            approved_text = approved_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise TranscriptionEvidenceError(
                "approved transcript must decode as strict UTF-8"
            ) from None
        if not approved_text.strip():
            raise TranscriptionEvidenceError(
                "approved transcript must be non-whitespace"
            )

    receipt_bytes = canonical_json_bytes(receipt.to_dict())
    values = _runtime_values(stage, approved_text=approved_text)
    evidence = object.__new__(TranscriptionEvidence)
    object.__setattr__(evidence, "attempt_id", receipt.attempt_id)
    object.__setattr__(evidence, "response_audio_ref", receipt.response_audio_ref)
    for name, value in values.items():
        object.__setattr__(evidence, name, value)
    object.__setattr__(evidence, "_receipt_bytes", receipt_bytes)
    object.__setattr__(
        evidence,
        "_approved_transcript_bytes",
        bytes(approved_bytes),
    )
    object.__setattr__(
        evidence,
        "_snapshot_bytes",
        _transcription_snapshot_bytes(
            attempt_id=receipt.attempt_id,
            response_audio_ref=receipt.response_audio_ref,
            runtime_values=values,
            receipt_bytes=receipt_bytes,
            approved_transcript_bytes=approved_bytes,
        ),
    )
    object.__setattr__(evidence, "_seal", _TRANSCRIPTION_SEAL)
    return evidence


def _require_transcription_evidence(
    value: object,
    *,
    attempt: ValidatedAttemptEvidence | None = None,
) -> TranscriptionEvidence:
    if type(value) is not TranscriptionEvidence:
        raise TypeError("transcription must be a TranscriptionEvidence")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError(
            "transcription evidence was not issued by load_transcription_evidence"
        ) from None
    if seal is not _TRANSCRIPTION_SEAL:
        raise TypeError(
            "transcription evidence was not issued by load_transcription_evidence"
        )
    if type(value._receipt_bytes) is not bytes:
        raise TranscriptionEvidenceError(
            "transcription evidence receipt identity is invalid"
        )
    receipt = _receipt_from_evidence(value)
    if (
        value.attempt_id != receipt.attempt_id
        or value.response_audio_ref != receipt.response_audio_ref
    ):
        raise TranscriptionEvidenceError(
            "transcription evidence does not match its durable receipt identity"
        )

    public_stage = _public_transcription_union(value)
    if public_stage != dict(receipt.transcription):
        raise TranscriptionEvidenceError(
            "transcription evidence runtime fields disagree with its durable receipt"
        )

    if type(value._approved_transcript_bytes) is not bytes:
        raise TranscriptionEvidenceError(
            "approved transcript issuance bytes are invalid"
        )
    if value.status == "SUCCESS":
        try:
            decoded = value._approved_transcript_bytes.decode(
                "utf-8",
                errors="strict",
            )
        except UnicodeDecodeError:
            raise TranscriptionEvidenceError(
                "approved transcript issuance bytes are not strict UTF-8"
            ) from None
        if not decoded.strip():
            raise TranscriptionEvidenceError(
                "approved transcript issuance bytes are whitespace-only"
            )
        expected_ref = (
            "sha256:"
            + hashlib.sha256(value._approved_transcript_bytes).hexdigest()
        )
        if (
            decoded != value.approved_transcript_text
            or expected_ref != value.approved_transcript_ref
        ):
            raise TranscriptionEvidenceError(
                "approved transcript text/ref disagree with issuance bytes"
            )
    elif (
        value._approved_transcript_bytes != b""
        or value.approved_transcript_text != ""
        or value.approved_transcript_ref != ""
    ):
        raise TranscriptionEvidenceError(
            "non-SUCCESS evidence cannot expose an approved transcript"
        )

    runtime_values = _all_runtime_values(value)
    if type(value._snapshot_bytes) is not bytes:
        raise TranscriptionEvidenceError(
            "transcription evidence issuance snapshot is invalid"
        )
    current_snapshot = _transcription_snapshot_bytes(
        attempt_id=value.attempt_id,
        response_audio_ref=value.response_audio_ref,
        runtime_values=runtime_values,
        receipt_bytes=value._receipt_bytes,
        approved_transcript_bytes=value._approved_transcript_bytes,
    )
    if current_snapshot != value._snapshot_bytes:
        raise TranscriptionEvidenceError(
            "transcription evidence runtime fields disagree with its issuance snapshot"
        )

    if attempt is not None:
        validated_attempt = _require_attempt_evidence(attempt)
        if validated_attempt.channel != "S":
            raise TranscriptionEvidenceError(
                "transcription evidence can bind only an S attempt"
            )
        if (
            value.attempt_id != validated_attempt.attempt_id
            or value.response_audio_ref
            != validated_attempt.response_artifact_ref
        ):
            raise TranscriptionEvidenceError(
                "transcription evidence does not bind this attempt and raw audio"
            )
    return value


def _transcription_identity(value: TranscriptionEvidence) -> bytes:
    validated = _require_transcription_evidence(value)
    return bytes(validated._receipt_bytes)


def _receipt_from_evidence(value: TranscriptionEvidence) -> TranscriptionReceipt:
    try:
        decoded = strict_json_loads(value._receipt_bytes)
        return _decode_transcription_record(
            decoded,
            location="transcription evidence receipt identity",
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise TranscriptionEvidenceError(
            "transcription evidence receipt identity is invalid"
        ) from exc


def _runtime_values(
    stage: Mapping[str, object],
    *,
    approved_text: str,
) -> dict[str, object]:
    return {
        "status": stage["status"],
        "stt_model_id": stage.get("stt_model_id", ""),
        "stt_model_version": stage.get("stt_model_version", ""),
        "decoder_version": stage.get("decoder_version", ""),
        "stt_output_ref": stage.get("stt_output_ref", ""),
        "approved_transcript_ref": stage.get(
            "approved_transcript_ref",
            "",
        ),
        "approved_transcript_text": approved_text,
        "verifier_id": stage.get("verifier_id", ""),
        "verifier_version": stage.get("verifier_version", 0),
        "uncertainty_code": stage.get("uncertainty_code", ""),
        "failure_code": stage.get("failure_code", ""),
    }


def _all_runtime_values(value: TranscriptionEvidence) -> dict[str, object]:
    return {
        "status": value.status,
        "stt_model_id": value.stt_model_id,
        "stt_model_version": value.stt_model_version,
        "decoder_version": value.decoder_version,
        "stt_output_ref": value.stt_output_ref,
        "approved_transcript_ref": value.approved_transcript_ref,
        "approved_transcript_text": value.approved_transcript_text,
        "verifier_id": value.verifier_id,
        "verifier_version": value.verifier_version,
        "uncertainty_code": value.uncertainty_code,
        "failure_code": value.failure_code,
    }


def _public_transcription_union(
    value: TranscriptionEvidence,
) -> dict[str, object]:
    if value.status == "SUCCESS":
        return {
            "status": value.status,
            "stt_model_id": value.stt_model_id,
            "stt_model_version": value.stt_model_version,
            "decoder_version": value.decoder_version,
            "stt_output_ref": value.stt_output_ref,
            "approved_transcript_ref": value.approved_transcript_ref,
            "verifier_id": value.verifier_id,
            "verifier_version": value.verifier_version,
        }
    if value.status == "UNCERTAIN":
        return {
            "status": value.status,
            "stt_model_id": value.stt_model_id,
            "stt_model_version": value.stt_model_version,
            "decoder_version": value.decoder_version,
            "stt_output_ref": value.stt_output_ref,
            "verifier_id": value.verifier_id,
            "verifier_version": value.verifier_version,
            "uncertainty_code": value.uncertainty_code,
        }
    if value.status == "FAILED":
        if (
            value.stt_model_id == ""
            and value.stt_model_version == ""
            and value.decoder_version == ""
        ):
            return {
                "status": value.status,
                "failure_code": value.failure_code,
            }
        return {
            "status": value.status,
            "stt_model_id": value.stt_model_id,
            "stt_model_version": value.stt_model_version,
            "decoder_version": value.decoder_version,
            "failure_code": value.failure_code,
        }
    raise TranscriptionEvidenceError(
        "transcription evidence status is invalid"
    )


def _transcription_snapshot_bytes(
    *,
    attempt_id: str,
    response_audio_ref: str,
    runtime_values: dict[str, object],
    receipt_bytes: bytes,
    approved_transcript_bytes: bytes,
) -> bytes:
    return canonical_json_bytes(
        {
            "domain": _TRANSCRIPTION_SNAPSHOT_DOMAIN,
            "v": _TRANSCRIPTION_SNAPSHOT_VERSION,
            "attempt_id": attempt_id,
            "response_audio_ref": response_audio_ref,
            **runtime_values,
            "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "approved_transcript_bytes_sha256": hashlib.sha256(
                approved_transcript_bytes
            ).hexdigest(),
        }
    )
