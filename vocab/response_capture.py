"""D60/D62 crash-safe fresh response capture and resumable bindings."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .artifact_store import ArtifactStore
from .capture_ledger import (
    CAPTURE_LEDGER_FILENAME,
    CAPTURE_LEDGER_VERSION,
    CaptureLedgerError,
    CaptureReceipt,
    append_capture_record,
    build_capture_receipt,
    read_capture_ledger,
    validate_capture_bindings,
)
from .contracts import ASSESSMENT_ATTEMPT_ID_PATTERN
from .exposure import DisplayPermit, ExposureReservation, read_exposure_ledger
from .t12_jsonl import validated_ledger_path


_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)


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

    ``no_historical_t12_state`` remains only the explicit T12.1 bootstrap
    assertion frozen by D62. It is not producer-history authority.
    """
    if type(no_historical_t12_state) is not bool:
        raise TypeError("no_historical_t12_state must be an actual bool")
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    exposure = validated_ledger_path(
        exposure_path,
        "exposure ledger",
        CaptureLedgerError,
    )
    capture = validated_ledger_path(
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
    validate_capture_bindings(
        captures,
        exposure_attempt_ids=tuple(item.attempt_id for item in exposure_history),
        artifact_store=artifact_store,
    )
    return captures


def capture_response(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    captured_at: object,
    display_permit: DisplayPermit,
    response_bytes: bytes,
) -> CaptureReceipt:
    """Create a fresh capture only from one consumed in-memory display permit."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")

    # D60 retains complete-history validation before fresh persistence.
    exposures = read_exposure_ledger(exposure_path)
    captures = validate_capture_ledger(
        capture_path,
        exposure_history=exposures,
        artifact_store=artifact_store,
    )
    if not isinstance(display_permit, DisplayPermit):
        raise TypeError("display_permit must be a DisplayPermit")
    if type(display_permit) is not DisplayPermit:
        raise TypeError("display_permit must be an exact issued DisplayPermit")
    attempt_id = DisplayPermit._validated_attempt_id_for_capture(display_permit)
    matching = [item for item in exposures if item.attempt_id == attempt_id]
    if len(matching) != 1:
        raise CaptureLedgerError(
            "capture requires exactly one compatible exposure reservation"
        )
    if any(receipt.attempt_id == attempt_id for receipt in captures):
        raise CaptureLedgerError("capture slot already exists for attempt")

    if type(response_bytes) is not bytes:
        raise TypeError("response_bytes must be exact bytes")
    expected_artifact_ref = "sha256:" + hashlib.sha256(response_bytes).hexdigest()
    receipt = build_capture_receipt(
        captured_at=captured_at,
        attempt_id=attempt_id,
        response_artifact_ref=expected_artifact_ref,
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
    append_capture_record(path, record)


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
