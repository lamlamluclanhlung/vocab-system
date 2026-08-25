"""D60 crash-safe response capture receipts and resumable bindings."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .artifact_store import ArtifactStore
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from .exposure import (
    ExposureReservation,
    _append_strict_canonical_record,
    _read_strict_canonical_jsonl,
    _validated_ledger_path,
    _validated_utc_timestamp,
    read_exposure_ledger,
)


CAPTURE_LEDGER_VERSION = 1
CAPTURE_LEDGER_FILENAME = "t12-captures.jsonl"

_CAPTURE_FIELDS = frozenset(
    (
        "v",
        "producer",
        "producer_version",
        "captured_at",
        "attempt_id",
        "response_artifact_ref",
    )
)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)


class CaptureLedgerError(ValueError):
    """Raised when D60 capture history or artifact binding is invalid."""


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    """One exact physical D60 attempt-to-response binding."""

    v: int
    producer: str
    producer_version: int
    captured_at: str
    attempt_id: str
    response_artifact_ref: str

    def to_dict(self) -> dict[str, object]:
        return {
            "v": self.v,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "captured_at": self.captured_at,
            "attempt_id": self.attempt_id,
            "response_artifact_ref": self.response_artifact_ref,
        }


@dataclass(frozen=True, slots=True)
class ResumableCapture:
    """A verified receipt plus its exact immutable learner-response bytes."""

    receipt: CaptureReceipt
    response_bytes: bytes


def initialize_t12_ledgers(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    no_historical_t12_state: bool,
) -> tuple[tuple[ExposureReservation, ...], tuple[CaptureReceipt, ...]]:
    """Safely create or validate the paired D55/D60 ledger boundary.

    ``no_historical_t12_state`` is an explicit caller assertion. It is required
    because T12.1 does not inspect EventLog and therefore must not guess whether
    absent ledgers are safe to create.
    """
    if type(no_historical_t12_state) is not bool:
        raise TypeError("no_historical_t12_state must be an actual bool")
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    exposure = _validated_ledger_path(
        exposure_path,
        "exposure ledger",
        CaptureLedgerError,
    )
    capture = _validated_ledger_path(
        capture_path,
        "capture ledger",
        CaptureLedgerError,
    )
    if exposure.resolve(strict=False) == capture.resolve(strict=False):
        raise CaptureLedgerError("exposure and capture ledgers need distinct paths")

    exposure_exists = exposure.is_file()
    capture_exists = capture.is_file()
    if exposure.exists() and not exposure_exists:
        raise CaptureLedgerError("exposure ledger path is not a regular file")
    if capture.exists() and not capture_exists:
        raise CaptureLedgerError("capture ledger path is not a regular file")

    if not exposure_exists and not capture_exists:
        if not no_historical_t12_state:
            raise CaptureLedgerError(
                "absent ledgers require explicit confirmation of no historical T12 state"
            )
        _create_empty_ledger(exposure)
        _create_empty_ledger(capture)
    elif exposure_exists and not capture_exists:
        exposures = read_exposure_ledger(exposure)
        if exposures:
            raise CaptureLedgerError(
                "non-empty exposure history exists without a capture ledger"
            )
        if not no_historical_t12_state:
            raise CaptureLedgerError(
                "missing capture ledger cannot be recreated without explicit empty-state authority"
            )
        _create_empty_ledger(capture)
    elif capture_exists and not exposure_exists:
        captures = read_capture_ledger(capture)
        if captures:
            raise CaptureLedgerError(
                "capture history exists without an exposure ledger"
            )
        if not no_historical_t12_state:
            raise CaptureLedgerError(
                "missing exposure ledger cannot be recreated without explicit empty-state authority"
            )
        _create_empty_ledger(exposure)

    exposures = read_exposure_ledger(exposure)
    captures = validate_capture_ledger(
        capture,
        exposure_history=exposures,
        artifact_store=artifact_store,
    )
    return exposures, captures


def read_capture_ledger(
    path: str | os.PathLike[str],
) -> tuple[CaptureReceipt, ...]:
    """Read the complete strict canonical D60 history in physical order."""
    records = _read_strict_canonical_jsonl(
        path,
        decoder=_decode_capture_record,
        error_type=CaptureLedgerError,
        ledger_name="capture ledger",
    )
    slots: set[tuple[str, int, str]] = set()
    for record in records:
        slot = (record.producer, record.producer_version, record.attempt_id)
        if slot in slots:
            raise CaptureLedgerError(
                "duplicate physical capture slot exists in the ledger"
            )
        slots.add(slot)
    return tuple(records)


def validate_capture_ledger(
    path: str | os.PathLike[str],
    *,
    exposure_history: tuple[ExposureReservation, ...] | list[ExposureReservation],
    artifact_store: ArtifactStore,
) -> tuple[CaptureReceipt, ...]:
    """Validate every receipt against exactly one reservation and exact bytes."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    for exposure in exposure_history:
        if not isinstance(exposure, ExposureReservation):
            raise TypeError("exposure_history contains a non-reservation value")
    captures = read_capture_ledger(path)
    for receipt in captures:
        matching = [
            exposure
            for exposure in exposure_history
            if exposure.attempt_id == receipt.attempt_id
        ]
        if len(matching) != 1:
            raise CaptureLedgerError(
                "capture receipt does not have exactly one compatible exposure reservation"
            )
        try:
            artifact_store.read(receipt.response_artifact_ref)
        except ValueError as exc:
            raise CaptureLedgerError(
                "capture receipt references a missing or corrupt artifact"
            ) from exc
    return captures


def capture_response(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    captured_at: object,
    attempt_id: object,
    response_bytes: bytes,
) -> CaptureReceipt:
    """Durably store exact learner bytes, then bind them to one reservation."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")

    # D60 freezes this exact validation and persistence ordering.
    exposures = read_exposure_ledger(exposure_path)
    captures = validate_capture_ledger(
        capture_path,
        exposure_history=exposures,
        artifact_store=artifact_store,
    )
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise CaptureLedgerError("attempt_id is invalid")
    matching = [item for item in exposures if item.attempt_id == attempt_id]
    if len(matching) != 1:
        raise CaptureLedgerError(
            "capture requires exactly one compatible exposure reservation"
        )
    if any(receipt.attempt_id == attempt_id for receipt in captures):
        raise CaptureLedgerError("capture slot already exists for attempt")

    if type(response_bytes) is not bytes:
        raise TypeError("response_bytes must be exact bytes")
    expected_artifact_ref = (
        "sha256:" + hashlib.sha256(response_bytes).hexdigest()
    )
    receipt = _decode_capture_record(
        {
            "v": CAPTURE_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "captured_at": captured_at,
            "attempt_id": attempt_id,
            "response_artifact_ref": expected_artifact_ref,
        },
        location="new capture receipt",
    )
    response_artifact_ref = artifact_store.put(response_bytes)
    if response_artifact_ref != expected_artifact_ref:
        raise CaptureLedgerError("artifact store returned an unexpected content ref")
    if artifact_store.read(response_artifact_ref) != response_bytes:
        raise CaptureLedgerError("response artifact exact readback failed")
    _append_capture_record(capture_path, receipt)
    return receipt


def resume_captured_response(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    attempt_id: object,
) -> ResumableCapture | None:
    """Resume IFF one valid receipt and its verified exact artifact exist."""
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise CaptureLedgerError("attempt_id is invalid")
    exposures = read_exposure_ledger(exposure_path)
    captures = validate_capture_ledger(
        capture_path,
        exposure_history=exposures,
        artifact_store=artifact_store,
    )
    matches = [receipt for receipt in captures if receipt.attempt_id == attempt_id]
    if not matches:
        return None
    if len(matches) != 1:  # pragma: no cover - duplicate reader closes this first
        raise CaptureLedgerError("capture slot is physically duplicated")
    receipt = matches[0]
    return ResumableCapture(
        receipt=receipt,
        response_bytes=artifact_store.read(receipt.response_artifact_ref),
    )


def _append_capture_record(
    path: str | os.PathLike[str],
    record: CaptureReceipt,
) -> None:
    _append_strict_canonical_record(
        path,
        record,
        reader=read_capture_ledger,
        error_type=CaptureLedgerError,
        ledger_name="capture ledger",
    )


def _decode_capture_record(value: object, *, location: str) -> CaptureReceipt:
    if type(value) is not dict or set(value) != _CAPTURE_FIELDS:
        raise CaptureLedgerError(f"{location} has the wrong capture key set")
    if type(value["v"]) is not int or value["v"] != CAPTURE_LEDGER_VERSION:
        raise CaptureLedgerError(f"{location} has an invalid version")
    if (
        type(value["producer"]) is not str
        or value["producer"] != T12_ASSESSMENT_PRODUCER_ID
    ):
        raise CaptureLedgerError(f"{location} has an invalid producer")
    if (
        type(value["producer_version"]) is not int
        or value["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise CaptureLedgerError(f"{location} has an invalid producer_version")
    captured_at = _validated_utc_timestamp(
        value["captured_at"],
        "captured_at",
        CaptureLedgerError,
    )
    attempt_id = value["attempt_id"]
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise CaptureLedgerError(f"{location} has an invalid attempt_id")
    artifact_ref = value["response_artifact_ref"]
    if type(artifact_ref) is not str or _ARTIFACT_REF_RE.fullmatch(artifact_ref) is None:
        raise CaptureLedgerError(f"{location} has an invalid response_artifact_ref")
    return CaptureReceipt(
        v=CAPTURE_LEDGER_VERSION,
        producer=T12_ASSESSMENT_PRODUCER_ID,
        producer_version=T12_ASSESSMENT_PRODUCER_VERSION,
        captured_at=captured_at,
        attempt_id=attempt_id,
        response_artifact_ref=artifact_ref,
    )


def _create_empty_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if not path.is_file():
            raise CaptureLedgerError("ledger path is not a regular file") from None
    except OSError as exc:
        raise CaptureLedgerError("empty ledger could not be durably initialized") from exc
