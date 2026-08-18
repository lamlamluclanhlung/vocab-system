"""Durable COMMIT_INTENT inspection and explicit evidence recovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..contracts import SOURCE_REF_PATTERN, UNIT_KEY_PATTERN
from ..models import Event
from .build import build_unit_key_query
from .event_payloads import acceptance_payload, is_lower_sha256, is_valid_attempt_id
from .ports import AnkiGateway, EventLogPort
from .request import RepairResult, RepairStatus

import re


_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SOURCE_REF_RE = re.compile(SOURCE_REF_PATTERN)


class HistoryError(ValueError):
    """Raised when durable FORGE history cannot be interpreted safely."""


class PendingStatus(str, Enum):
    NONE = "NONE"
    ONE = "ONE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class PendingIntent:
    status: PendingStatus
    forge_attempt_id: str = ""
    intent: Event | None = None


def read_forge_events(event_log: EventLogPort) -> tuple[Event, ...]:
    events = event_log.read(event_type="FORGE")
    if not isinstance(events, list) or any(
        not isinstance(event, Event) or event.event != "FORGE" for event in events
    ):
        raise HistoryError("EventLog returned malformed FORGE history")
    return tuple(events)


def _events_by_attempt(events: tuple[Event, ...]) -> dict[str, list[Event]]:
    grouped: dict[str, list[Event]] = {}
    for event in events:
        attempt_id = event.payload.get("forge_attempt_id")
        if isinstance(attempt_id, str):
            grouped.setdefault(attempt_id, []).append(event)
    return grouped


def _attempt_parts(
    attempt_events: list[Event],
) -> tuple[list[Event], list[Event], list[Event]]:
    intents = [
        event
        for event in attempt_events
        if event.payload.get("outcome") == "COMMIT_INTENT"
    ]
    acceptances = [
        event for event in attempt_events if event.payload.get("accepted") is True
    ]
    abandonments = [
        event
        for event in attempt_events
        if event.payload.get("outcome") == "INTENT_ABANDONED"
    ]
    return intents, acceptances, abandonments


def inspect_pending_intent(
    events: tuple[Event, ...],
    unit_key: str,
) -> PendingIntent:
    pending: list[tuple[str, Event]] = []
    ambiguous = False

    for attempt_id, attempt_events in _events_by_attempt(events).items():
        intents, acceptances, abandonments = _attempt_parts(attempt_events)
        if not any(intent.unit_key == unit_key for intent in intents):
            continue
        if (
            not is_valid_attempt_id(attempt_id)
            or len(intents) != 1
            or len(acceptances) > 1
            or len(abandonments) > 1
            or (acceptances and abandonments)
        ):
            ambiguous = True
            continue
        if not acceptances and not abandonments:
            pending.append((attempt_id, intents[0]))

    for event in events:
        if (
            event.unit_key == unit_key
            and event.payload.get("outcome") == "COMMIT_INTENT"
            and not is_valid_attempt_id(event.payload.get("forge_attempt_id"))
        ):
            ambiguous = True

    if ambiguous or len(pending) > 1:
        return PendingIntent(PendingStatus.AMBIGUOUS)
    if len(pending) == 1:
        attempt_id, intent = pending[0]
        return PendingIntent(PendingStatus.ONE, attempt_id, intent)
    return PendingIntent(PendingStatus.NONE)


def _history_for_attempt(
    events: tuple[Event, ...],
    forge_attempt_id: str,
) -> tuple[Event | None, bool, bool, bool]:
    attempt_events = _events_by_attempt(events).get(forge_attempt_id, [])
    intents, acceptances, abandonments = _attempt_parts(attempt_events)
    ambiguous = (
        len(intents) > 1
        or len(acceptances) > 1
        or len(abandonments) > 1
        or bool(acceptances and abandonments)
    )
    intent = intents[0] if len(intents) == 1 else None
    return intent, bool(acceptances), bool(abandonments), ambiguous


def _durable_intent_values(intent: Event) -> tuple[str, str] | None:
    source_ref = intent.payload.get("source_ref")
    structured_hash = intent.payload.get("structured_output_sha256")
    if (
        _UNIT_KEY_RE.fullmatch(intent.unit_key) is None
        or not isinstance(source_ref, str)
        or _SOURCE_REF_RE.fullmatch(source_ref) is None
        or not is_lower_sha256(structured_hash)
    ):
        return None
    return source_ref, structured_hash


def _find_note_ids(anki: AnkiGateway, unit_key: str) -> tuple[int, ...]:
    result = anki.find_notes(build_unit_key_query(unit_key))
    if not isinstance(result, list) or any(type(note_id) is not int for note_id in result):
        raise ValueError("Anki returned malformed note IDs")
    return tuple(result)


def repair_evidence(
    *,
    forge_attempt_id: str,
    anki: AnkiGateway,
    event_log: EventLogPort,
) -> RepairResult:
    """Repair one missing acceptance using only its durable COMMIT_INTENT."""
    if not is_valid_attempt_id(forge_attempt_id):
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            outcome="ATTEMPT_ID_INVALID",
        )
    try:
        events = read_forge_events(event_log)
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="EVENTLOG_UNAVAILABLE",
        )

    intent, accepted, abandoned, ambiguous = _history_for_attempt(
        events, forge_attempt_id
    )
    if ambiguous:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="HISTORY_AMBIGUOUS",
        )
    if intent is None:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="INTENT_MISSING",
        )
    if accepted or abandoned:
        return RepairResult(
            RepairStatus.ALREADY_RESOLVED,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="ALREADY_ACCEPTED" if accepted else "INTENT_ABANDONED",
        )

    durable_values = _durable_intent_values(intent)
    if durable_values is None:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="INTENT_INVALID",
        )
    source_ref, structured_hash = durable_values
    try:
        note_ids = _find_note_ids(anki, intent.unit_key)
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="ANKI_READ_FAILED",
        )
    if not note_ids:
        return RepairResult(
            RepairStatus.NO_NOTE,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="PENDING_INTENT",
        )
    if len(note_ids) > 1:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="PENDING_INTENT_AMBIGUOUS",
            ambiguous_note_ids=note_ids,
        )

    note_id = note_ids[0]
    payload = acceptance_payload(
        source_ref=source_ref,
        forge_attempt_id=forge_attempt_id,
        note_id=note_id,
        structured_output_sha256=structured_hash,
        repaired=True,
    )
    try:
        stored = event_log.log("FORGE", intent.unit_key, payload)
        if not isinstance(stored, Event):
            raise HistoryError("EventLog returned a malformed event")
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            note_id=note_id,
            outcome="ACCEPTANCE_UNWRITABLE",
        )
    return RepairResult(
        RepairStatus.REPAIRED,
        forge_attempt_id=forge_attempt_id,
        unit_key=intent.unit_key,
        note_id=note_id,
        outcome="REPAIRED",
    )


def abandon_intent(
    *,
    forge_attempt_id: str,
    reason: str,
    anki: AnkiGateway,
    event_log: EventLogPort,
) -> RepairResult:
    """Explicitly abandon a pending intent only after proving no note exists."""
    if (
        not is_valid_attempt_id(forge_attempt_id)
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            outcome="REQUEST_INVALID",
        )
    try:
        events = read_forge_events(event_log)
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="EVENTLOG_UNAVAILABLE",
        )

    intent, accepted, abandoned, ambiguous = _history_for_attempt(
        events, forge_attempt_id
    )
    if ambiguous:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="HISTORY_AMBIGUOUS",
        )
    if intent is None:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            outcome="INTENT_MISSING",
        )
    if accepted or abandoned:
        return RepairResult(
            RepairStatus.ALREADY_RESOLVED,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="ALREADY_ACCEPTED" if accepted else "INTENT_ABANDONED",
        )
    if _durable_intent_values(intent) is None:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="INTENT_INVALID",
        )

    try:
        note_ids = _find_note_ids(anki, intent.unit_key)
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="ANKI_READ_FAILED",
        )
    if note_ids:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            note_id=note_ids[0] if len(note_ids) == 1 else None,
            outcome="REPAIR_REQUIRED" if len(note_ids) == 1 else "PENDING_INTENT_AMBIGUOUS",
            ambiguous_note_ids=note_ids if len(note_ids) > 1 else (),
        )

    payload = {
        "source_ref": intent.payload["source_ref"],
        "accepted": False,
        "outcome": "INTENT_ABANDONED",
        "forge_attempt_id": forge_attempt_id,
        "reason": reason,
    }
    try:
        stored = event_log.log("FORGE", intent.unit_key, payload)
        if not isinstance(stored, Event):
            raise HistoryError("EventLog returned a malformed event")
    except Exception:
        return RepairResult(
            RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=intent.unit_key,
            outcome="EVENTLOG_UNAVAILABLE",
        )
    return RepairResult(
        RepairStatus.ABANDONED,
        forge_attempt_id=forge_attempt_id,
        unit_key=intent.unit_key,
        outcome="INTENT_ABANDONED",
    )
