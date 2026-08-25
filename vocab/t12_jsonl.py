"""Private behavior-neutral strict canonical JSONL primitives for T12."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, TypeVar

from .artifact_json import ArtifactJSONError, canonical_json_bytes, strict_json_loads


class _CanonicalRecord(Protocol):
    def to_dict(self) -> dict[str, object]: ...


_RecordT = TypeVar("_RecordT", bound=_CanonicalRecord)


def read_strict_canonical_jsonl(
    path: str | os.PathLike[str],
    *,
    decoder: Callable[..., _RecordT],
    error_type: type[ValueError],
    ledger_name: str,
) -> list[_RecordT]:
    """Read every physical canonical JSONL record or fail closed."""
    ledger_path = validated_ledger_path(path, ledger_name, error_type)
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


def append_strict_canonical_record(
    path: str | os.PathLike[str],
    record: _RecordT,
    *,
    reader: Callable[[str | os.PathLike[str]], Sequence[_RecordT]],
    error_type: type[ValueError],
    ledger_name: str,
) -> None:
    """Append, flush/fsync, then exactly read back one canonical record."""
    ledger_path = validated_ledger_path(path, ledger_name, error_type)
    line = canonical_json_bytes(record.to_dict()) + b"\n"
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


def validated_ledger_path(
    path: str | os.PathLike[str],
    ledger_name: str,
    error_type: type[ValueError],
) -> Path:
    """Require one explicit ledger file path without creating it."""
    if path is None:
        raise TypeError(f"{ledger_name} path must be explicit")
    result = Path(path)
    if not result.name:
        raise error_type(f"{ledger_name} path must identify a file")
    return result


def validated_utc_timestamp(
    value: object,
    name: str,
    error_type: type[ValueError],
) -> str:
    """Require normalized ISO-8601 UTC with an explicit ``+00:00`` offset."""
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
