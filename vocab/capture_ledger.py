"""Strict D60 capture-ledger records, serialization, and binding checks."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .artifact_store import ArtifactStore
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from .t12_jsonl import (
    append_strict_canonical_record,
    read_strict_canonical_jsonl,
    validated_utc_timestamp,
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


def read_capture_ledger(
    path: str | os.PathLike[str],
) -> tuple[CaptureReceipt, ...]:
    """Read the complete strict canonical D60 history in physical order."""
    records = read_strict_canonical_jsonl(
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


def validate_capture_bindings(
    captures: Sequence[CaptureReceipt],
    *,
    exposure_attempt_ids: Sequence[str],
    artifact_store: ArtifactStore,
) -> None:
    """Require every capture to bind one exposure and one verified artifact."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    exposure_counts = Counter(exposure_attempt_ids)
    for receipt in captures:
        if not isinstance(receipt, CaptureReceipt):
            raise TypeError("captures contains a non-receipt value")
        if exposure_counts[receipt.attempt_id] != 1:
            raise CaptureLedgerError(
                "capture receipt does not have exactly one compatible exposure reservation"
            )
        try:
            artifact_store.read(receipt.response_artifact_ref)
        except ValueError as exc:
            raise CaptureLedgerError(
                "capture receipt references a missing or corrupt artifact"
            ) from exc


def build_capture_receipt(
    *,
    captured_at: object,
    attempt_id: object,
    response_artifact_ref: object,
) -> CaptureReceipt:
    """Validate and construct the exact closed D60 receipt record."""
    return _decode_capture_record(
        {
            "v": CAPTURE_LEDGER_VERSION,
            "producer": T12_ASSESSMENT_PRODUCER_ID,
            "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
            "captured_at": captured_at,
            "attempt_id": attempt_id,
            "response_artifact_ref": response_artifact_ref,
        },
        location="new capture receipt",
    )


def append_capture_record(
    path: str | os.PathLike[str],
    record: CaptureReceipt,
) -> None:
    """Durably append and exactly read back one validated capture receipt."""
    if not isinstance(record, CaptureReceipt):
        raise TypeError("record must be a CaptureReceipt")
    append_strict_canonical_record(
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
    captured_at = validated_utc_timestamp(
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
