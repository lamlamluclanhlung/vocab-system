"""Strict D65 transcription receipts, canonical JSONL, and durable binding."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .artifact_store import ArtifactStore
from .capture_ledger import (
    CaptureReceipt,
    read_capture_ledger,
    validate_capture_bindings,
)
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    SLUG_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from .exposure import ExposureReservation, read_exposure_ledger
from .t12_jsonl import (
    append_strict_canonical_record,
    read_strict_canonical_jsonl,
    validated_utc_timestamp,
)


TRANSCRIPTION_LEDGER_VERSION = 1
TRANSCRIPTION_LEDGER_FILENAME = "t12-transcriptions.jsonl"

TRANSCRIPTION_UNCERTAINTY_CODES = (
    "audio_unclear",
    "transcript_ambiguous",
    "stt_human_disagreement",
)
TRANSCRIPTION_FAILURE_CODES = (
    "transcription_failed",
    "audio_unusable",
    "infrastructure_failure",
)

_RECEIPT_FIELDS = frozenset(
    (
        "v",
        "producer",
        "producer_version",
        "recorded_at",
        "attempt_id",
        "response_audio_ref",
        "transcription",
    )
)
_SUCCESS_FIELDS = frozenset(
    (
        "status",
        "stt_model_id",
        "stt_model_version",
        "decoder_version",
        "stt_output_ref",
        "approved_transcript_ref",
        "verifier_id",
        "verifier_version",
    )
)
_UNCERTAIN_FIELDS = frozenset(
    (
        "status",
        "stt_model_id",
        "stt_model_version",
        "decoder_version",
        "stt_output_ref",
        "verifier_id",
        "verifier_version",
        "uncertainty_code",
    )
)
_FAILED_BEFORE_STT_FIELDS = frozenset(("status", "failure_code"))
_FAILED_AFTER_STT_FIELDS = frozenset(
    (
        "status",
        "stt_model_id",
        "stt_model_version",
        "decoder_version",
        "failure_code",
    )
)
_STT_METADATA_FIELDS = (
    "stt_model_id",
    "stt_model_version",
    "decoder_version",
)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)
_VERIFIER_ID_RE = re.compile(SLUG_PATTERN)


class TranscriptionLedgerError(ValueError):
    """Raised when D65 history, union closure, or artifact binding is invalid."""


class _FrozenTranscription(dict[str, object]):
    """A copied receipt union that cannot be changed in place."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("transcription receipt data is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


@dataclass(frozen=True, slots=True)
class TranscriptionReceipt:
    """One exact terminal transcription disposition for one captured S attempt."""

    v: int
    producer: str
    producer_version: int
    recorded_at: str
    attempt_id: str
    response_audio_ref: str
    transcription: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transcription",
            _FrozenTranscription(self.transcription),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "v": self.v,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "recorded_at": self.recorded_at,
            "attempt_id": self.attempt_id,
            "response_audio_ref": self.response_audio_ref,
            "transcription": dict(self.transcription),
        }


def read_transcription_ledger(
    path: str | os.PathLike[str],
) -> tuple[TranscriptionReceipt, ...]:
    """Read complete strict canonical D65 history in physical order."""
    records = read_strict_canonical_jsonl(
        path,
        decoder=_decode_transcription_record,
        error_type=TranscriptionLedgerError,
        ledger_name="transcription ledger",
    )
    slots: set[tuple[str, int, str]] = set()
    for record in records:
        slot = (record.producer, record.producer_version, record.attempt_id)
        if slot in slots:
            raise TranscriptionLedgerError(
                "duplicate physical transcription slot exists in the ledger"
            )
        slots.add(slot)
    return tuple(records)


def build_transcription_receipt(
    *,
    recorded_at: object,
    attempt_id: object,
    response_audio_ref: object,
    transcription: object,
) -> TranscriptionReceipt:
    """Validate and construct one exact closed D65 receipt record."""
    return _decode_transcription_record(
        {
            "v": TRANSCRIPTION_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "recorded_at": recorded_at,
            "attempt_id": attempt_id,
            "response_audio_ref": response_audio_ref,
            "transcription": transcription,
        },
        location="new transcription receipt",
    )


def validate_transcription_bindings(
    receipts: Sequence[TranscriptionReceipt],
    *,
    exposure_history: Sequence[ExposureReservation],
    capture_history: Sequence[CaptureReceipt],
    artifact_store: ArtifactStore,
) -> None:
    """Bind each terminal disposition to one S exposure, capture, and artifacts."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    for exposure in exposure_history:
        if not isinstance(exposure, ExposureReservation):
            raise TypeError("exposure_history contains a non-reservation value")
    for capture in capture_history:
        if not isinstance(capture, CaptureReceipt):
            raise TypeError("capture_history contains a non-receipt value")

    exposure_counts = Counter(item.attempt_id for item in exposure_history)
    capture_counts = Counter(item.attempt_id for item in capture_history)
    exposures_by_attempt = {item.attempt_id: item for item in exposure_history}
    captures_by_attempt = {item.attempt_id: item for item in capture_history}

    for receipt in receipts:
        if not isinstance(receipt, TranscriptionReceipt):
            raise TypeError("receipts contains a non-receipt value")
        validated = _decode_transcription_record(
            receipt.to_dict(),
            location="transcription receipt binding",
        )
        if exposure_counts[validated.attempt_id] != 1:
            raise TranscriptionLedgerError(
                "transcription receipt requires exactly one exposure reservation"
            )
        if capture_counts[validated.attempt_id] != 1:
            raise TranscriptionLedgerError(
                "transcription receipt requires exactly one capture receipt"
            )
        exposure = exposures_by_attempt[validated.attempt_id]
        capture = captures_by_attempt[validated.attempt_id]
        if exposure.channel != "S":
            raise TranscriptionLedgerError(
                "transcription receipt capture does not belong to an S attempt"
            )
        if validated.response_audio_ref != capture.response_artifact_ref:
            raise TranscriptionLedgerError(
                "response_audio_ref does not match the capture receipt"
            )
        _verified_artifact(
            artifact_store,
            validated.response_audio_ref,
            "raw audio",
        )

        stage = validated.transcription
        if stage["status"] == "SUCCESS":
            _verified_text_artifact(
                artifact_store,
                stage["stt_output_ref"],
                "STT output",
            )
            _verified_text_artifact(
                artifact_store,
                stage["approved_transcript_ref"],
                "approved transcript",
            )
        elif stage["status"] == "UNCERTAIN":
            _verified_text_artifact(
                artifact_store,
                stage["stt_output_ref"],
                "STT output",
            )


def validate_transcription_ledger(
    path: str | os.PathLike[str],
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
) -> tuple[TranscriptionReceipt, ...]:
    """Validate complete D55/D60/D65 history and every artifact binding."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    try:
        exposures = read_exposure_ledger(exposure_path)
        captures = read_capture_ledger(capture_path)
        validate_capture_bindings(
            captures,
            exposure_attempt_ids=tuple(item.attempt_id for item in exposures),
            artifact_store=artifact_store,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise TranscriptionLedgerError(
            "transcription authority requires valid exposure and capture history"
        ) from exc
    receipts = read_transcription_ledger(path)
    validate_transcription_bindings(
        receipts,
        exposure_history=exposures,
        capture_history=captures,
        artifact_store=artifact_store,
    )
    return receipts


def append_transcription_record(
    path: str | os.PathLike[str],
    record: TranscriptionReceipt,
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
) -> None:
    """Validate complete authorities, append once, fsync, and exactly read back."""
    if type(record) is not TranscriptionReceipt:
        raise TypeError("record must be a TranscriptionReceipt")
    validated_record = _decode_transcription_record(
        record.to_dict(),
        location="new transcription receipt",
    )
    history = validate_transcription_ledger(
        path,
        exposure_path=exposure_path,
        capture_path=capture_path,
        artifact_store=artifact_store,
    )
    slot = (
        validated_record.producer,
        validated_record.producer_version,
        validated_record.attempt_id,
    )
    if any(
        (item.producer, item.producer_version, item.attempt_id) == slot
        for item in history
    ):
        raise TranscriptionLedgerError(
            "transcription slot already has a durable terminal disposition"
        )

    exposures = read_exposure_ledger(exposure_path)
    captures = read_capture_ledger(capture_path)
    validate_capture_bindings(
        captures,
        exposure_attempt_ids=tuple(item.attempt_id for item in exposures),
        artifact_store=artifact_store,
    )
    validate_transcription_bindings(
        (*history, validated_record),
        exposure_history=exposures,
        capture_history=captures,
        artifact_store=artifact_store,
    )
    append_strict_canonical_record(
        path,
        validated_record,
        reader=read_transcription_ledger,
        error_type=TranscriptionLedgerError,
        ledger_name="transcription ledger",
    )


def _decode_transcription_record(
    value: object,
    *,
    location: str,
) -> TranscriptionReceipt:
    if type(value) is not dict or set(value) != _RECEIPT_FIELDS:
        raise TranscriptionLedgerError(
            f"{location} has the wrong transcription receipt key set"
        )
    if (
        type(value["v"]) is not int
        or value["v"] != TRANSCRIPTION_LEDGER_VERSION
    ):
        raise TranscriptionLedgerError(f"{location} has an invalid version")
    if value["producer"] != T12_ASSESSMENT_PRODUCER_ID:
        raise TranscriptionLedgerError(f"{location} has an invalid producer")
    if (
        type(value["producer_version"]) is not int
        or value["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise TranscriptionLedgerError(
            f"{location} has an invalid producer_version"
        )
    recorded_at = validated_utc_timestamp(
        value["recorded_at"],
        "recorded_at",
        TranscriptionLedgerError,
    )
    attempt_id = _require_pattern(
        value["attempt_id"],
        _ATTEMPT_ID_RE,
        f"{location} attempt_id",
    )
    response_audio_ref = _require_pattern(
        value["response_audio_ref"],
        _ARTIFACT_REF_RE,
        f"{location} response_audio_ref",
    )
    transcription = _validated_transcription_union(
        value["transcription"],
        location=f"{location} transcription",
    )
    return TranscriptionReceipt(
        v=TRANSCRIPTION_LEDGER_VERSION,
        producer=T12_ASSESSMENT_PRODUCER_ID,
        producer_version=T12_ASSESSMENT_PRODUCER_VERSION,
        recorded_at=recorded_at,
        attempt_id=attempt_id,
        response_audio_ref=response_audio_ref,
        transcription=transcription,
    )


def _validated_transcription_union(
    value: object,
    *,
    location: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TranscriptionLedgerError(f"{location} must be an object")
    status = value.get("status")
    if type(status) is not str:
        raise TranscriptionLedgerError(f"{location} has an invalid status")
    if status == "SUCCESS":
        expected_fields = _SUCCESS_FIELDS
    elif status == "UNCERTAIN":
        expected_fields = _UNCERTAIN_FIELDS
    elif status == "FAILED":
        if set(value) == _FAILED_BEFORE_STT_FIELDS:
            expected_fields = _FAILED_BEFORE_STT_FIELDS
        else:
            expected_fields = _FAILED_AFTER_STT_FIELDS
    else:
        raise TranscriptionLedgerError(f"{location} has an invalid status")
    if set(value) != expected_fields:
        raise TranscriptionLedgerError(f"{location} has the wrong key set")

    result = dict(value)
    if status in ("SUCCESS", "UNCERTAIN") or expected_fields == _FAILED_AFTER_STT_FIELDS:
        for name in _STT_METADATA_FIELDS:
            _require_nonempty_string(result[name], f"{location}.{name}")
    if status in ("SUCCESS", "UNCERTAIN"):
        _require_pattern(
            result["stt_output_ref"],
            _ARTIFACT_REF_RE,
            f"{location}.stt_output_ref",
        )
        verifier_id = result["verifier_id"]
        if (
            type(verifier_id) is not str
            or _VERIFIER_ID_RE.fullmatch(verifier_id) is None
        ):
            raise TranscriptionLedgerError(
                f"{location}.verifier_id is invalid"
            )
        verifier_version = result["verifier_version"]
        if type(verifier_version) is not int or verifier_version < 1:
            raise TranscriptionLedgerError(
                f"{location}.verifier_version must be a positive integer"
            )
    if status == "SUCCESS":
        _require_pattern(
            result["approved_transcript_ref"],
            _ARTIFACT_REF_RE,
            f"{location}.approved_transcript_ref",
        )
    elif status == "UNCERTAIN":
        if (
            type(result["uncertainty_code"]) is not str
            or result["uncertainty_code"] not in TRANSCRIPTION_UNCERTAINTY_CODES
        ):
            raise TranscriptionLedgerError(
                f"{location}.uncertainty_code is invalid"
            )
    else:
        if (
            type(result["failure_code"]) is not str
            or result["failure_code"] not in TRANSCRIPTION_FAILURE_CODES
        ):
            raise TranscriptionLedgerError(
                f"{location}.failure_code is invalid"
            )
    return result


def _verified_artifact(
    artifact_store: ArtifactStore,
    ref: object,
    name: str,
) -> bytes:
    try:
        return artifact_store.read(ref)
    except (TypeError, ValueError) as exc:
        raise TranscriptionLedgerError(
            f"{name} artifact is missing or corrupt"
        ) from exc


def _verified_text_artifact(
    artifact_store: ArtifactStore,
    ref: object,
    name: str,
) -> str:
    raw = _verified_artifact(artifact_store, ref, name)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise TranscriptionLedgerError(
            f"{name} artifact must be strict UTF-8"
        ) from None
    if not text.strip():
        raise TranscriptionLedgerError(
            f"{name} artifact must be a non-whitespace string"
        )
    return text


def _require_nonempty_string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise TranscriptionLedgerError(f"{name} must be a non-empty string")
    return value


def _require_pattern(
    value: object,
    pattern: re.Pattern[str],
    name: str,
) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise TranscriptionLedgerError(f"{name} is invalid")
    return value
