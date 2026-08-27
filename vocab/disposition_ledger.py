"""Strict D67 pre-capture terminal disposition ledger and binding checks."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .contracts import (
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from .t12_jsonl import (
    append_strict_canonical_record,
    read_strict_canonical_jsonl,
    validated_utc_timestamp,
)


DISPOSITION_LEDGER_VERSION = 1
DISPOSITION_LEDGER_FILENAME = "t12-dispositions.jsonl"

DISPOSITION_CODES = frozenset(
    (
        "refusal",
        "explicit_skip",
        "no_response",
        "invalid_artifact",
        "infrastructure_failure",
    )
)

_DISPOSITION_FIELDS = frozenset(
    (
        "v",
        "producer",
        "producer_version",
        "disposed_at",
        "attempt_id",
        "disposition_code",
    )
)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)


class DispositionLedgerError(ValueError):
    """Raised when D67 pre-capture disposition history cannot be trusted."""


@dataclass(frozen=True, slots=True)
class OperationalDispositionReceipt:
    """One exact physical D67 pre-capture terminal disposition."""

    v: int
    producer: str
    producer_version: int
    disposed_at: str
    attempt_id: str
    disposition_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "v": self.v,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "disposed_at": self.disposed_at,
            "attempt_id": self.attempt_id,
            "disposition_code": self.disposition_code,
        }


def read_disposition_ledger(
    path: str | os.PathLike[str],
) -> tuple[OperationalDispositionReceipt, ...]:
    """Read the complete strict canonical D67 history in physical order."""
    records = read_strict_canonical_jsonl(
        path,
        decoder=_decode_disposition_record,
        error_type=DispositionLedgerError,
        ledger_name="disposition ledger",
    )
    slots: set[tuple[str, int, str]] = set()
    for record in records:
        slot = (record.producer, record.producer_version, record.attempt_id)
        if slot in slots:
            raise DispositionLedgerError(
                "duplicate physical disposition slot exists in the ledger"
            )
        slots.add(slot)
    return tuple(records)


def validate_disposition_bindings(
    dispositions: Sequence[OperationalDispositionReceipt],
    *,
    exposure_attempt_ids: Sequence[str],
    exposure_channel_by_attempt_id: Mapping[str, str],
    capture_attempt_ids: Sequence[str],
) -> None:
    """Require every disposition to bind exactly one R/L/W exposure and no capture."""
    exposure_counts = Counter(exposure_attempt_ids)
    capture_counts = Counter(capture_attempt_ids)
    for receipt in dispositions:
        if not isinstance(receipt, OperationalDispositionReceipt):
            raise TypeError("dispositions contains a non-receipt value")
        if exposure_counts[receipt.attempt_id] != 1:
            raise DispositionLedgerError(
                "disposition receipt does not have exactly one compatible "
                "exposure reservation"
            )
        channel = exposure_channel_by_attempt_id.get(receipt.attempt_id)
        if channel not in ("R", "L", "W"):
            raise DispositionLedgerError(
                "disposition receipt is bound to a non-R/L/W exposure channel"
            )
        if capture_counts[receipt.attempt_id] != 0:
            raise DispositionLedgerError(
                "disposition receipt coexists with a capture receipt for the "
                "same attempt (capture/disposition mutual exclusion violated)"
            )


def build_disposition_receipt(
    *,
    disposed_at: object,
    attempt_id: object,
    disposition_code: object,
) -> OperationalDispositionReceipt:
    """Validate and construct the exact closed D67 receipt record."""
    return _decode_disposition_record(
        {
            "v": DISPOSITION_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "disposed_at": disposed_at,
            "attempt_id": attempt_id,
            "disposition_code": disposition_code,
        },
        location="new disposition receipt",
    )


def append_disposition_record(
    path: str | os.PathLike[str],
    record: OperationalDispositionReceipt,
) -> None:
    """Durably append and exactly read back one validated disposition receipt."""
    if not isinstance(record, OperationalDispositionReceipt):
        raise TypeError("record must be an OperationalDispositionReceipt")
    append_strict_canonical_record(
        path,
        record,
        reader=read_disposition_ledger,
        error_type=DispositionLedgerError,
        ledger_name="disposition ledger",
    )


def _decode_disposition_record(
    value: object,
    *,
    location: str,
) -> OperationalDispositionReceipt:
    if type(value) is not dict or set(value) != _DISPOSITION_FIELDS:
        raise DispositionLedgerError(f"{location} has the wrong disposition key set")
    if type(value["v"]) is not int or value["v"] != DISPOSITION_LEDGER_VERSION:
        raise DispositionLedgerError(f"{location} has an invalid version")
    if (
        type(value["producer"]) is not str
        or value["producer"] != T12_ASSESSMENT_PRODUCER_ID
    ):
        raise DispositionLedgerError(f"{location} has an invalid producer")
    if (
        type(value["producer_version"]) is not int
        or value["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise DispositionLedgerError(f"{location} has an invalid producer_version")
    disposed_at = validated_utc_timestamp(
        value["disposed_at"],
        "disposed_at",
        DispositionLedgerError,
    )
    attempt_id = value["attempt_id"]
    if type(attempt_id) is not str or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise DispositionLedgerError(f"{location} has an invalid attempt_id")
    disposition_code = value["disposition_code"]
    if type(disposition_code) is not str or disposition_code not in DISPOSITION_CODES:
        raise DispositionLedgerError(f"{location} has an invalid disposition_code")
    return OperationalDispositionReceipt(
        v=DISPOSITION_LEDGER_VERSION,
        producer=T12_ASSESSMENT_PRODUCER_ID,
        producer_version=T12_ASSESSMENT_PRODUCER_VERSION,
        disposed_at=disposed_at,
        attempt_id=attempt_id,
        disposition_code=disposition_code,
    )
