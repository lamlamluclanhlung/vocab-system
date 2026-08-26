"""D60/D62 crash-safe fresh response capture and resumable bindings.

Also implements the D67 pre-capture terminal disposition operations that
share this exposure/capture/disposition boundary: ``record_refusal``,
``record_explicit_skip``, and ``close_text_submission``.
"""

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
from .disposition_ledger import (
    DispositionLedgerError,
    OperationalDispositionReceipt,
    append_disposition_record,
    build_disposition_receipt,
    read_disposition_ledger,
)
from .exposure import DisplayPermit, ExposureReservation, read_exposure_ledger, validate_t12_histories
from .t12_jsonl import validated_ledger_path


_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_CAPTURE_TERMINAL_FAILURE_ISSUER = object()


class _BoundedCaptureReceiptCommitFailure(CaptureLedgerError):
    """Private signal for one post-validated R/L/W capture-commit failure."""

    __slots__ = ("_issuer",)

    def __new__(
        cls,
        *_args: object,
        **_kwargs: object,
    ) -> _BoundedCaptureReceiptCommitFailure:
        raise TypeError(
            "bounded capture-receipt failure signals are capture-subsystem issued"
        )


@dataclass(frozen=True, slots=True)
class ResumableCapture:
    """A verified receipt plus its exact immutable learner-response bytes."""

    receipt: CaptureReceipt
    response_bytes: bytes


def initialize_t12_ledgers(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    no_historical_t12_state: bool,
) -> tuple[
    tuple[ExposureReservation, ...],
    tuple[CaptureReceipt, ...],
    tuple[OperationalDispositionReceipt, ...],
]:
    """Safely create or validate the triple D55/D60/D67 ledger boundary.

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
    disposition = validated_ledger_path(
        disposition_path,
        "disposition ledger",
        CaptureLedgerError,
    )
    resolved = {
        exposure.resolve(strict=False),
        capture.resolve(strict=False),
        disposition.resolve(strict=False),
    }
    if len(resolved) != 3:
        raise CaptureLedgerError(
            "exposure, capture, and disposition ledgers need distinct paths"
        )

    paths = {"exposure": exposure, "capture": capture, "disposition": disposition}
    exists: dict[str, bool] = {}
    for name, path in paths.items():
        is_file = path.is_file()
        if path.exists() and not is_file:
            raise CaptureLedgerError(f"{name} ledger path is not a regular file")
        exists[name] = is_file

    if not any(exists.values()):
        if not no_historical_t12_state:
            raise CaptureLedgerError(
                "absent ledgers require explicit confirmation of no historical T12 state"
            )
        for path in paths.values():
            _create_empty_ledger(path)
    elif not all(exists.values()):
        if exists["exposure"] and read_exposure_ledger(exposure):
            raise CaptureLedgerError(
                "non-empty exposure history exists without the complete "
                "exposure/capture/disposition boundary"
            )
        if exists["capture"] and read_capture_ledger(capture):
            raise CaptureLedgerError(
                "capture history exists without the complete "
                "exposure/capture/disposition boundary"
            )
        if exists["disposition"] and read_disposition_ledger(disposition):
            raise CaptureLedgerError(
                "disposition history exists without the complete "
                "exposure/capture/disposition boundary"
            )
        if not no_historical_t12_state:
            raise CaptureLedgerError(
                "missing ledgers cannot be recreated without explicit empty-state authority"
            )
        for name, path in paths.items():
            if not exists[name]:
                _create_empty_ledger(path)

    return validate_t12_histories(
        exposure_path=exposure,
        capture_path=capture,
        disposition_path=disposition,
        artifact_store=artifact_store,
    )


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
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    captured_at: object,
    display_permit: DisplayPermit,
    response_bytes: bytes,
) -> CaptureReceipt:
    """Create a fresh capture only from one consumed in-memory display permit."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")

    # D60/D67 retain complete-history validation before fresh persistence.
    exposures, captures, dispositions = validate_t12_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
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
    if any(receipt.attempt_id == attempt_id for receipt in dispositions):
        raise CaptureLedgerError(
            "capture cannot be recorded for an attempt with an existing disposition"
        )

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
    try:
        _append_capture_record(capture_path, receipt)
    except CaptureLedgerError as commit_error:
        _raise_if_bounded_capture_receipt_commit_failure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            attempt_id=attempt_id,
            commit_error=commit_error,
        )
    return receipt


def resume_captured_response(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    attempt_id: object,
) -> ResumableCapture | None:
    """Resume IFF one valid receipt and its verified exact artifact exist."""
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise CaptureLedgerError("attempt_id is invalid")
    _exposures, captures, _dispositions = validate_t12_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
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


def record_refusal(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    display_permit: DisplayPermit,
    disposed_at: object,
) -> OperationalDispositionReceipt:
    """Record one durable D67 explicit-refusal pre-capture disposition."""
    return _append_disposition(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
        display_permit=display_permit,
        disposed_at=disposed_at,
        disposition_code="refusal",
    )


def record_explicit_skip(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    display_permit: DisplayPermit,
    disposed_at: object,
) -> OperationalDispositionReceipt:
    """Record one durable D67 explicit-skip pre-capture disposition."""
    return _append_disposition(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
        display_permit=display_permit,
        disposed_at=disposed_at,
        disposition_code="explicit_skip",
    )


def close_text_submission(
    *,
    raw_bytes: bytes | None,
    display_permit: DisplayPermit,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    captured_at: object,
    disposed_at: object,
) -> CaptureReceipt | OperationalDispositionReceipt:
    """Classify one raw R/L/W text submission per the frozen D67 table.

    Classification order is exact: ``None`` -> ``no_response``; a non-bytes
    value -> ``TypeError``; a strict-UTF-8 decode failure ->
    ``invalid_artifact``; an all-whitespace decode -> ``no_response``;
    otherwise the exact original ``raw_bytes`` are captured unchanged. The
    decode step is classification-only and is never persisted, re-encoded,
    stripped, or normalized.
    """
    if raw_bytes is None:
        return _append_disposition(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            display_permit=display_permit,
            disposed_at=disposed_at,
            disposition_code="no_response",
        )
    if type(raw_bytes) is not bytes:
        raise TypeError("raw_bytes must be exact bytes or None")
    try:
        decoded = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _append_disposition(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            display_permit=display_permit,
            disposed_at=disposed_at,
            disposition_code="invalid_artifact",
        )
    if decoded.strip() == "":
        return _append_disposition(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            display_permit=display_permit,
            disposed_at=disposed_at,
            disposition_code="no_response",
        )
    try:
        return capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            captured_at=captured_at,
            display_permit=display_permit,
            response_bytes=raw_bytes,
        )
    except _BoundedCaptureReceiptCommitFailure as terminal_failure:
        return _record_capture_subsystem_infrastructure_failure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=artifact_store,
            display_permit=display_permit,
            disposed_at=disposed_at,
            terminal_failure=terminal_failure,
        )


def _record_capture_subsystem_infrastructure_failure(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    display_permit: DisplayPermit,
    disposed_at: object,
    terminal_failure: _BoundedCaptureReceiptCommitFailure,
) -> OperationalDispositionReceipt:
    """Record the sole bounded D67 capture-subsystem terminal failure."""
    _require_bounded_capture_receipt_commit_failure(terminal_failure)
    return _append_disposition(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
        display_permit=display_permit,
        disposed_at=disposed_at,
        disposition_code="infrastructure_failure",
        terminal_failure=terminal_failure,
    )


def _append_disposition(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    display_permit: DisplayPermit,
    disposed_at: object,
    disposition_code: str,
    terminal_failure: _BoundedCaptureReceiptCommitFailure | None = None,
) -> OperationalDispositionReceipt:
    ordinary_codes = {
        "refusal",
        "explicit_skip",
        "no_response",
        "invalid_artifact",
    }
    if disposition_code == "infrastructure_failure":
        _require_bounded_capture_receipt_commit_failure(terminal_failure)
    elif disposition_code not in ordinary_codes or terminal_failure is not None:
        raise DispositionLedgerError(
            "capture subsystem has no authorized origin path for this disposition"
        )
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    exposures, captures, dispositions = validate_t12_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
    )
    if type(display_permit) is not DisplayPermit:
        raise TypeError("display_permit must be an exact issued DisplayPermit")
    attempt_id = DisplayPermit._validated_attempt_id_for_disposition(display_permit)
    matching = [item for item in exposures if item.attempt_id == attempt_id]
    if len(matching) != 1:
        raise DispositionLedgerError(
            "disposition requires exactly one compatible exposure reservation"
        )
    if matching[0].channel not in ("R", "L", "W"):
        raise DispositionLedgerError(
            "disposition cannot be recorded for a non-R/L/W exposure channel"
        )
    if any(receipt.attempt_id == attempt_id for receipt in captures):
        raise DispositionLedgerError(
            "disposition cannot be recorded for an attempt with an existing capture"
        )
    if any(receipt.attempt_id == attempt_id for receipt in dispositions):
        raise DispositionLedgerError("disposition slot already exists for attempt")
    receipt = build_disposition_receipt(
        disposed_at=disposed_at,
        attempt_id=attempt_id,
        disposition_code=disposition_code,
    )
    _append_disposition_record(disposition_path, receipt)
    return receipt


def _raise_if_bounded_capture_receipt_commit_failure(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    attempt_id: str,
    commit_error: CaptureLedgerError,
) -> None:
    """Raise the private signal only for an exact valid R/L/W 1/0/0 state."""
    exposures, captures, dispositions = validate_t12_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
    )
    matching_exposures = [
        record for record in exposures if record.attempt_id == attempt_id
    ]
    matching_captures = [
        record for record in captures if record.attempt_id == attempt_id
    ]
    matching_dispositions = [
        record for record in dispositions if record.attempt_id == attempt_id
    ]
    if (
        len(matching_exposures) == 1
        and matching_exposures[0].channel in ("R", "L", "W")
        and not matching_captures
        and not matching_dispositions
    ):
        raise _issue_bounded_capture_receipt_commit_failure(commit_error) from commit_error
    raise commit_error


def _issue_bounded_capture_receipt_commit_failure(
    commit_error: CaptureLedgerError,
) -> _BoundedCaptureReceiptCommitFailure:
    failure = BaseException.__new__(_BoundedCaptureReceiptCommitFailure)
    BaseException.__init__(
        failure,
        f"bounded capture-receipt commit failure: {commit_error}",
    )
    failure._issuer = _CAPTURE_TERMINAL_FAILURE_ISSUER
    return failure


def _require_bounded_capture_receipt_commit_failure(value: object) -> None:
    try:
        issuer = value._issuer
    except AttributeError:
        issuer = None
    if type(value) is not _BoundedCaptureReceiptCommitFailure or (
        issuer is not _CAPTURE_TERMINAL_FAILURE_ISSUER
    ):
        raise TypeError(
            "terminal_failure was not issued by the bounded capture-commit path"
        )


def _append_capture_record(
    path: str | os.PathLike[str],
    record: CaptureReceipt,
) -> None:
    append_capture_record(path, record)


def _append_disposition_record(
    path: str | os.PathLike[str],
    record: OperationalDispositionReceipt,
) -> None:
    append_disposition_record(path, record)


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
