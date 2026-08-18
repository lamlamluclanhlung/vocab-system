"""Append-only JSONL persistence for vocabulary events."""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    CHANNELS,
    EVENT_DAY_FORMAT,
    EVENT_LOCAL_TIMEZONE,
    EVENT_REQUIRED_FIELDS,
    EVENT_SCHEMA_VERSION,
    EVENT_TYPES,
    EVENTS_REQUIRING_MODEL_METADATA,
    MODEL_METADATA_FIELDS,
)
from .models import Event


class EventLogCorruptionError(ValueError):
    """Raised when an event log contains data that cannot be trusted."""


class EventLogCorruptionWarning(UserWarning):
    """Warn that a malformed final record was ignored while reading."""


class UnsupportedEventVersionError(ValueError):
    """Raised when no explicit decoder exists for an event schema version."""

    def __init__(self, version: int, *, location: str) -> None:
        self.version = version
        super().__init__(
            f"unsupported event schema version {version} in {location}"
        )


class EventTimezoneFallbackWarning(RuntimeWarning):
    """Warn that the host lacks IANA data and a fixed local offset is in use."""


try:
    _LOCAL_TIMEZONE = ZoneInfo(EVENT_LOCAL_TIMEZONE)
except ZoneInfoNotFoundError:
    warnings.warn(
        f"IANA data for {EVENT_LOCAL_TIMEZONE} is unavailable; using the "
        "contemporary fixed UTC+07:00 offset",
        EventTimezoneFallbackWarning,
        stacklevel=2,
    )
    _LOCAL_TIMEZONE = timezone(timedelta(hours=7), EVENT_LOCAL_TIMEZONE)


_REQUIRED_FIELDS = frozenset(EVENT_REQUIRED_FIELDS)
_TAIL_READ_CHUNK_SIZE = 8192


def _now_utc() -> datetime:
    """Clock seam used by tests; always return the current UTC instant."""
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 string")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include an explicit timezone offset")
    return parsed.astimezone(timezone.utc)


def _validate_event_values(event: str, unit_key: str, payload: dict[str, Any]) -> None:
    if event not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {event!r}")
    if not isinstance(unit_key, str) or not unit_key:
        raise ValueError("unit_key must be a non-empty string")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    if event in EVENTS_REQUIRING_MODEL_METADATA:
        for field_name in MODEL_METADATA_FIELDS:
            value = payload.get(field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{event} payload requires non-empty {field_name}"
                )

    if event == "STATE":
        channel = payload.get("channel")
        if channel not in CHANNELS:
            raise ValueError(f"STATE payload channel must be one of {CHANNELS}")


def _decode_v1_event(record: dict[str, Any], *, location: str) -> Event:
    """Decode and validate the version 1 event envelope."""
    version = record["v"]
    try:
        _validate_event_values(record["event"], record["unit_key"], record["payload"])
        timestamp = _parse_timestamp(record["ts"], field_name="event ts")
    except (TypeError, ValueError) as exc:
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: {exc}"
        ) from exc

    if record["ts"] != timestamp.isoformat():
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: ts must be normalized "
            "to UTC with +00:00 offset"
        )

    expected_day = timestamp.astimezone(_LOCAL_TIMEZONE).strftime(EVENT_DAY_FORMAT)
    if record["day"] != expected_day:
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: day does not match ts "
            f"in {EVENT_LOCAL_TIMEZONE}"
        )

    return Event(
        v=version,
        ts=record["ts"],
        day=record["day"],
        event=record["event"],
        unit_key=record["unit_key"],
        payload=dict(record["payload"]),
    )


# Decoders are explicit by version so a future schema cannot be silently
# interpreted using version 1 rules. Add migrations/decoders only when their
# contracts exist.
_EVENT_DECODERS = {1: _decode_v1_event}


def _event_from_record(record: object, *, location: str) -> Event:
    if not isinstance(record, dict):
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: expected a JSON object"
        )
    missing_fields = _REQUIRED_FIELDS.difference(record)
    if missing_fields:
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: missing required fields "
            f"{tuple(sorted(missing_fields))}"
        )

    version = record["v"]
    if type(version) is not int:
        raise EventLogCorruptionError(
            f"invalid event envelope in {location}: v must be an integer"
        )
    decoder = _EVENT_DECODERS.get(version)
    if decoder is None:
        raise UnsupportedEventVersionError(version, location=location)
    return decoder(record, location=location)


class EventLog:
    """An append-only JSONL event log at an explicit filesystem path."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if path is None:
            raise TypeError("path must be explicit")
        self.path = Path(path)
        if not self.path.name:
            raise ValueError("path must identify an event log file")

        # Append mode creates a missing file without truncating an existing one.
        with self.path.open("a", encoding="utf-8"):
            pass

    def log(self, event: str, unit_key: str, payload: dict[str, Any]) -> Event:
        """Validate, construct, and append one complete event record."""
        _validate_event_values(event, unit_key, payload)

        instant = _now_utc()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("event clock returned a naive datetime")
        instant = instant.astimezone(timezone.utc)
        stored_event = Event(
            v=EVENT_SCHEMA_VERSION,
            ts=instant.isoformat(),
            day=instant.astimezone(_LOCAL_TIMEZONE).strftime(EVENT_DAY_FORMAT),
            event=event,
            unit_key=unit_key,
            payload=dict(payload),
        )

        # Serialize before opening for append so encoding/type failures cannot
        # leave a partial prefix in the log.
        line = json.dumps(stored_event.to_dict(), ensure_ascii=False)
        self._validate_trailing_record_for_append()
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return stored_event

    def read(
        self,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[Event]:
        """Read valid events in file order, with optional inclusive filters."""
        if event_type is not None and event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type!r}")
        since_utc = (
            None if since is None else _parse_timestamp(since, field_name="since")
        )

        lines = self._read_lines()
        events: list[Event] = []
        for index, line in enumerate(lines, start=1):
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                if index == len(lines):
                    warnings.warn(
                        f"ignoring malformed final event log record on line {index}",
                        EventLogCorruptionWarning,
                        stacklevel=2,
                    )
                    break
                raise EventLogCorruptionError(
                    f"invalid UTF-8 in event log on line {index}"
                ) from exc

            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                if index == len(lines):
                    warnings.warn(
                        f"ignoring malformed final event log record on line {index}",
                        EventLogCorruptionWarning,
                        stacklevel=2,
                    )
                    break
                raise EventLogCorruptionError(
                    f"malformed JSON in event log on line {index}"
                ) from exc

            stored_event = _event_from_record(record, location=f"line {index}")
            event_instant = _parse_timestamp(stored_event.ts, field_name="event ts")
            if event_type is not None and stored_event.event != event_type:
                continue
            if since_utc is not None and event_instant < since_utc:
                continue
            events.append(stored_event)
        return events

    def _read_lines(self) -> list[bytes]:
        data = self.path.read_bytes()
        if not data:
            return []

        lines = data.split(b"\n")
        if lines[-1] == b"":
            lines.pop()
        return lines

    def _validate_trailing_record_for_append(self) -> None:
        final_record = self._read_trailing_record()
        if final_record is None:
            return
        try:
            text = final_record.decode("utf-8")
            record = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EventLogCorruptionError(
                "refusing to append after a malformed final record"
            ) from exc
        _event_from_record(record, location="final record")

    def _read_trailing_record(self) -> bytes | None:
        """Read only the final newline-terminated record, scanning backward."""
        with self.path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            if file_size == 0:
                return None

            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                raise EventLogCorruptionError(
                    "refusing to append after a final record that is not "
                    "newline-terminated"
                )

            record_end = file_size - 1
            position = record_end
            chunks: list[bytes] = []
            while position > 0:
                read_size = min(_TAIL_READ_CHUNK_SIZE, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                previous_newline = chunk.rfind(b"\n")
                if previous_newline >= 0:
                    chunks.append(chunk[previous_newline + 1 :])
                    break
                chunks.append(chunk)

            return b"".join(reversed(chunks))
