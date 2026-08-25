"""Strict D55 exposure history and reserve-before-display permits."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from .artifact_json import ArtifactJSONError, canonical_json_bytes, strict_json_loads
from .artifact_store import ArtifactStore
from .assessment_identity import assessment_attempt_id
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)
from .session import SESSION_ID_PATTERN


EXPOSURE_LEDGER_VERSION = 1
EXPOSURE_LEDGER_FILENAME = "t12-exposures.jsonl"

_EXPOSURE_FIELDS = frozenset(
    (
        "v",
        "producer",
        "producer_version",
        "reserved_at",
        "attempt_id",
        "session_id",
        "item_ordinal",
        "unit_key",
        "channel",
        "presented_stimulus_ref",
        "stimulus_artifact_ref",
    )
)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_STIMULUS_REF_RE = re.compile(ASSESSMENT_STIMULUS_REF_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)
_PERMIT_ISSUER = object()


class ExposureLedgerError(ValueError):
    """Raised when D55 exposure history or reservation cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ExposureReservation:
    """One exact physical D55 exposure reservation."""

    v: int
    producer: str
    producer_version: int
    reserved_at: str
    attempt_id: str
    session_id: str
    item_ordinal: int
    unit_key: str
    channel: str
    presented_stimulus_ref: str
    stimulus_artifact_ref: str

    def to_dict(self) -> dict[str, object]:
        return {
            "v": self.v,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "reserved_at": self.reserved_at,
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "item_ordinal": self.item_ordinal,
            "unit_key": self.unit_key,
            "channel": self.channel,
            "presented_stimulus_ref": self.presented_stimulus_ref,
            "stimulus_artifact_ref": self.stimulus_artifact_ref,
        }


class DisplayPermit:
    """A one-use in-memory authorization issued only after durable readback."""

    __slots__ = ("_attempt_id", "_consumed", "_novel")

    def __init__(
        self,
        attempt_id: str,
        novel: bool,
        *,
        _issuer: object | None = None,
    ) -> None:
        if _issuer is not _PERMIT_ISSUER:
            raise TypeError("DisplayPermit can only be issued by reserve_exposure")
        self._attempt_id = attempt_id
        self._novel = novel
        self._consumed = False

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def novel(self) -> bool:
        return self._novel

    @property
    def consumed(self) -> bool:
        return self._consumed

    def consume(self) -> None:
        """Consume this exact display authority once."""
        if self._consumed:
            raise ExposureLedgerError("DisplayPermit has already been consumed")
        self._consumed = True


def read_exposure_ledger(
    path: str | os.PathLike[str],
) -> tuple[ExposureReservation, ...]:
    """Read the complete strict canonical D55 history in physical order."""
    records = _read_strict_canonical_jsonl(
        path,
        decoder=_decode_exposure_record,
        error_type=ExposureLedgerError,
        ledger_name="exposure ledger",
    )
    slots: set[tuple[str, int, str]] = set()
    for record in records:
        slot = (record.producer, record.producer_version, record.attempt_id)
        if slot in slots:
            raise ExposureLedgerError(
                "duplicate physical exposure slot exists in the ledger"
            )
        slots.add(slot)
    return tuple(records)


def exposure_is_novel(
    history: Sequence[ExposureReservation],
    current: ExposureReservation,
) -> bool:
    """Apply D55 novelty using only earlier physical reservations."""
    if not isinstance(current, ExposureReservation):
        raise TypeError("current must be an ExposureReservation")
    for earlier in history:
        if not isinstance(earlier, ExposureReservation):
            raise TypeError("history must contain ExposureReservation values")
        if (
            earlier.attempt_id != current.attempt_id
            and earlier.unit_key == current.unit_key
            and earlier.channel == current.channel
            and earlier.presented_stimulus_ref == current.presented_stimulus_ref
        ):
            return False
    return True


def reserve_exposure(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    reserved_at: object,
    attempt_id: object,
    session_id: object,
    item_ordinal: object,
    unit_key: object,
    channel: object,
    presented_stimulus_ref: object,
    stimulus_artifact_ref: object,
) -> DisplayPermit:
    """Durably reserve one exposure, then and only then issue a permit."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    history = read_exposure_ledger(exposure_path)

    # D60 requires both ledgers to remain a validated pair before exposure.
    from .response_capture import validate_capture_ledger

    validate_capture_ledger(
        capture_path,
        exposure_history=history,
        artifact_store=artifact_store,
    )
    record = _decode_exposure_record(
        {
            "v": EXPOSURE_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "reserved_at": reserved_at,
            "attempt_id": attempt_id,
            "session_id": session_id,
            "item_ordinal": item_ordinal,
            "unit_key": unit_key,
            "channel": channel,
            "presented_stimulus_ref": presented_stimulus_ref,
            "stimulus_artifact_ref": stimulus_artifact_ref,
        },
        location="new exposure reservation",
    )
    if any(existing.attempt_id == record.attempt_id for existing in history):
        raise ExposureLedgerError("exposure slot is already reserved")

    # A displayed artifact must itself be exact and available before reservation.
    artifact_store.read(record.stimulus_artifact_ref)
    novel = exposure_is_novel(history, record)
    _append_exposure_record(exposure_path, record)
    return DisplayPermit(record.attempt_id, novel, _issuer=_PERMIT_ISSUER)


def _append_exposure_record(
    path: str | os.PathLike[str],
    record: ExposureReservation,
) -> None:
    _append_strict_canonical_record(
        path,
        record,
        reader=read_exposure_ledger,
        error_type=ExposureLedgerError,
        ledger_name="exposure ledger",
    )


def _decode_exposure_record(
    value: object,
    *,
    location: str,
) -> ExposureReservation:
    if type(value) is not dict or set(value) != _EXPOSURE_FIELDS:
        raise ExposureLedgerError(f"{location} has the wrong exposure key set")
    if type(value["v"]) is not int or value["v"] != EXPOSURE_LEDGER_VERSION:
        raise ExposureLedgerError(f"{location} has an invalid version")
    if (
        type(value["producer"]) is not str
        or value["producer"] != T12_ASSESSMENT_PRODUCER_ID
    ):
        raise ExposureLedgerError(f"{location} has an invalid producer")
    if (
        type(value["producer_version"]) is not int
        or value["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise ExposureLedgerError(f"{location} has an invalid producer_version")
    reserved_at = _validated_utc_timestamp(
        value["reserved_at"],
        "reserved_at",
        ExposureLedgerError,
    )
    attempt_id = value["attempt_id"]
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise ExposureLedgerError(f"{location} has an invalid attempt_id")
    session_id = value["session_id"]
    if type(session_id) is not str or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise ExposureLedgerError(f"{location} has an invalid session_id")
    ordinal = value["item_ordinal"]
    if type(ordinal) is not int or ordinal < 0:
        raise ExposureLedgerError(
            f"{location} item_ordinal must be an actual non-negative integer"
        )
    unit_key = value["unit_key"]
    if type(unit_key) is not str or _UNIT_KEY_RE.fullmatch(unit_key) is None:
        raise ExposureLedgerError(f"{location} has an invalid unit_key")
    channel = value["channel"]
    if type(channel) is not str or channel not in ASSESSMENT_TASK_KIND_BY_CHANNEL:
        raise ExposureLedgerError(f"{location} has an invalid channel")
    stimulus_ref = value["presented_stimulus_ref"]
    if type(stimulus_ref) is not str or _STIMULUS_REF_RE.fullmatch(stimulus_ref) is None:
        raise ExposureLedgerError(
            f"{location} has an invalid presented_stimulus_ref"
        )
    artifact_ref = value["stimulus_artifact_ref"]
    if type(artifact_ref) is not str or _ARTIFACT_REF_RE.fullmatch(artifact_ref) is None:
        raise ExposureLedgerError(f"{location} has an invalid stimulus_artifact_ref")
    expected_attempt_id = assessment_attempt_id(
        session_id=session_id,
        item_ordinal=ordinal,
        unit_key=unit_key,
        channel=channel,
        presented_stimulus_ref=stimulus_ref,
    )
    if attempt_id != expected_attempt_id:
        raise ExposureLedgerError(
            f"{location} attempt_id does not match its frozen projection"
        )
    return ExposureReservation(
        v=EXPOSURE_LEDGER_VERSION,
        producer=T12_ASSESSMENT_PRODUCER_ID,
        producer_version=T12_ASSESSMENT_PRODUCER_VERSION,
        reserved_at=reserved_at,
        attempt_id=attempt_id,
        session_id=session_id,
        item_ordinal=ordinal,
        unit_key=unit_key,
        channel=channel,
        presented_stimulus_ref=stimulus_ref,
        stimulus_artifact_ref=artifact_ref,
    )


_RecordT = TypeVar("_RecordT")


def _read_strict_canonical_jsonl(
    path: str | os.PathLike[str],
    *,
    decoder: Callable[..., _RecordT],
    error_type: type[ValueError],
    ledger_name: str,
) -> list[_RecordT]:
    ledger_path = _validated_ledger_path(path, ledger_name, error_type)
    try:
        raw = ledger_path.read_bytes()
    except (OSError, FileNotFoundError, IsADirectoryError) as exc:
        raise error_type(f"{ledger_name} is missing or unreadable") from exc
    if raw and not raw.endswith(b"\n"):
        raise error_type(f"{ledger_name} has a malformed final record")
    records: list[_RecordT] = []
    for line_number, physical_line in enumerate(raw.splitlines(keepends=True), 1):
        body = physical_line[:-1]
        if not body:
            raise error_type(f"{ledger_name} record {line_number} is blank")
        try:
            value = strict_json_loads(body)
        except (ArtifactJSONError, TypeError) as exc:
            raise error_type(
                f"{ledger_name} record {line_number} is invalid: {exc}"
            ) from None
        record = decoder(value, location=f"{ledger_name} record {line_number}")
        expected_line = canonical_json_bytes(record.to_dict()) + b"\n"
        if physical_line != expected_line:
            raise error_type(
                f"{ledger_name} record {line_number} is not canonical JSONL"
            )
        records.append(record)
    return records


def _append_strict_canonical_record(
    path: str | os.PathLike[str],
    record: _RecordT,
    *,
    reader: Callable[[str | os.PathLike[str]], Sequence[_RecordT]],
    error_type: type[ValueError],
    ledger_name: str,
) -> None:
    ledger_path = _validated_ledger_path(path, ledger_name, error_type)
    line = canonical_json_bytes(record.to_dict()) + b"\n"  # type: ignore[attr-defined]
    try:
        with ledger_path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise error_type(f"{ledger_name} append failed") from exc
    history = reader(ledger_path)
    if not history or history[-1] != record:
        raise error_type(f"{ledger_name} exact append readback failed")


def _validated_ledger_path(
    path: str | os.PathLike[str],
    ledger_name: str,
    error_type: type[ValueError],
) -> Path:
    if path is None:
        raise TypeError(f"{ledger_name} path must be explicit")
    result = Path(path)
    if not result.name:
        raise error_type(f"{ledger_name} path must identify a file")
    return result


def _validated_utc_timestamp(
    value: object,
    name: str,
    error_type: type[ValueError],
) -> str:
    if type(value) is not str or not value:
        raise error_type(f"{name} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise error_type(f"{name} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise error_type(f"{name} must use explicit UTC +00:00")
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise error_type(f"{name} must be normalized UTC with +00:00")
    return value
