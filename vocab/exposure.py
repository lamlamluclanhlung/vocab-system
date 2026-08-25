"""Strict D55 exposure history and reserve-before-display permits."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .artifact_store import ArtifactStore
from .assessment_identity import assessment_attempt_id
from .capture_ledger import read_capture_ledger, validate_capture_bindings
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)
from .session import SESSION_ID_PATTERN, load_session_manifest
from .t12_jsonl import (
    append_strict_canonical_record,
    read_strict_canonical_jsonl,
    validated_utc_timestamp,
)


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

    __slots__ = ("_attempt_id", "_consumed", "_issuer", "_novel")

    def __new__(cls, *_args: object, **_kwargs: object) -> DisplayPermit:
        raise TypeError("DisplayPermit can only be issued by reserve_exposure")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("DisplayPermit bindings are immutable")

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
        object.__setattr__(self, "_consumed", True)

    def _validated_attempt_id_for_capture(self) -> str:
        try:
            issuer = object.__getattribute__(self, "_issuer")
        except AttributeError:
            raise TypeError(
                "display_permit was not issued by reserve_exposure"
            ) from None
        if type(self) is not DisplayPermit or issuer is not _PERMIT_ISSUER:
            raise TypeError("display_permit was not issued by reserve_exposure")
        if not self._consumed:
            raise ExposureLedgerError(
                "DisplayPermit must be consumed before response capture"
            )
        return self._attempt_id


def read_exposure_ledger(
    path: str | os.PathLike[str],
) -> tuple[ExposureReservation, ...]:
    """Read the complete strict canonical D55 history in physical order."""
    records = read_strict_canonical_jsonl(
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


def novelty_for_reserved_attempt(
    exposure_path: str | os.PathLike[str],
    attempt_id: object,
) -> bool:
    """Compute D55 novelty only for one verified physical reservation."""
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise ExposureLedgerError("attempt_id is invalid")
    history = read_exposure_ledger(exposure_path)
    matching_indexes = [
        index for index, record in enumerate(history) if record.attempt_id == attempt_id
    ]
    if len(matching_indexes) != 1:
        raise ExposureLedgerError(
            "novelty requires exactly one durable current reservation"
        )
    current_index = matching_indexes[0]
    current = history[current_index]
    for earlier in history[:current_index]:
        if (
            earlier.unit_key == current.unit_key
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
    session_root: str | os.PathLike[str],
    session_id: object,
    item_ordinal: object,
    reserved_at: object,
) -> DisplayPermit:
    """Reserve one exact persisted manifest item, then issue a permit."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    history = _validate_paired_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        artifact_store=artifact_store,
    )
    if type(item_ordinal) is not int or item_ordinal < 0:
        raise ExposureLedgerError(
            "item_ordinal must be an actual non-negative integer"
        )
    manifest = load_session_manifest(session_root, session_id)
    manifest_data = manifest.to_dict()
    items = manifest_data["items"]
    if type(items) is not list:  # pragma: no cover - manifest import guarantees it
        raise AssertionError("validated session manifest items are not an array")
    matching_items = [
        item
        for item in items
        if type(item) is dict and item.get("item_ordinal") == item_ordinal
    ]
    if len(matching_items) != 1:
        raise ExposureLedgerError(
            "session manifest does not contain exactly one requested item_ordinal"
        )
    item = matching_items[0]
    unit_key = item["unit_key"]
    channel = item["channel"]
    presented_stimulus_ref = item["presented_stimulus_ref"]
    stimulus_artifact_ref = item["stimulus_artifact_ref"]
    attempt_id = assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=item_ordinal,
        unit_key=unit_key,
        channel=channel,
        presented_stimulus_ref=presented_stimulus_ref,
    )
    record = _decode_exposure_record(
        {
            "v": EXPOSURE_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "reserved_at": reserved_at,
            "attempt_id": attempt_id,
            "session_id": manifest.session_id,
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

    artifact_store.read(record.stimulus_artifact_ref)
    _append_exposure_record(exposure_path, record)
    _validate_paired_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        artifact_store=artifact_store,
    )
    novel = novelty_for_reserved_attempt(exposure_path, record.attempt_id)
    return _issue_display_permit(record.attempt_id, novel)


def _issue_display_permit(attempt_id: str, novel: bool) -> DisplayPermit:
    permit = object.__new__(DisplayPermit)
    object.__setattr__(permit, "_attempt_id", attempt_id)
    object.__setattr__(permit, "_novel", novel)
    object.__setattr__(permit, "_consumed", False)
    object.__setattr__(permit, "_issuer", _PERMIT_ISSUER)
    return permit


def _validate_paired_histories(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
) -> tuple[ExposureReservation, ...]:
    exposures = read_exposure_ledger(exposure_path)
    captures = read_capture_ledger(capture_path)
    validate_capture_bindings(
        captures,
        exposure_attempt_ids=tuple(item.attempt_id for item in exposures),
        artifact_store=artifact_store,
    )
    return exposures


def _append_exposure_record(
    path: str | os.PathLike[str],
    record: ExposureReservation,
) -> None:
    append_strict_canonical_record(
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
    reserved_at = validated_utc_timestamp(
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
