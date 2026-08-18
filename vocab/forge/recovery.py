"""Explicit T6 recovery operations for pending Forge intents."""

from __future__ import annotations

from .build import unit_key_query
from .event_payloads import acceptance_payload, abandoned_payload, analyze_attempts
from .ports import AnkiGateway, EventLogPort
from .request import RepairResult, RepairStatus


def _state_for(event_log: EventLogPort, attempt_id: str):
    states = analyze_attempts(event_log.read(event_type="FORGE"))
    state = states.get(attempt_id)
    if state is None or state.intent is None:
        raise ValueError(f"no unique COMMIT_INTENT for {attempt_id!r}")
    return state


def repair_evidence(
    *,
    forge_attempt_id: str,
    anki: AnkiGateway,
    event_log: EventLogPort,
) -> RepairResult:
    """Repair one pending acceptance from durable intent plus current Anki state."""
    state = _state_for(event_log, forge_attempt_id)
    if state.acceptance is not None or state.abandoned is not None:
        note_id = None
        if state.acceptance is not None:
            candidate = state.acceptance.payload.get("note_id")
            if type(candidate) is int:
                note_id = candidate
        return RepairResult(
            status=RepairStatus.ALREADY_RESOLVED,
            forge_attempt_id=forge_attempt_id,
            unit_key=state.unit_key,
            note_id=note_id,
        )

    note_ids = tuple(anki.find_notes(unit_key_query(state.unit_key)))
    if not note_ids:
        return RepairResult(
            status=RepairStatus.NO_NOTE,
            forge_attempt_id=forge_attempt_id,
            unit_key=state.unit_key,
        )
    if len(note_ids) > 1:
        return RepairResult(
            status=RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=state.unit_key,
            ambiguous_note_ids=note_ids,
        )

    intent_payload = state.intent.payload
    source_ref = intent_payload["source_ref"]
    output_hash = intent_payload["structured_output_sha256"]
    note_id = note_ids[0]
    event_log.log(
        "FORGE",
        state.unit_key,
        acceptance_payload(
            source_ref=source_ref,
            attempt_id=forge_attempt_id,
            note_id=note_id,
            structured_output_sha256=output_hash,
            repaired=True,
        ),
    )
    return RepairResult(
        status=RepairStatus.REPAIRED,
        forge_attempt_id=forge_attempt_id,
        unit_key=state.unit_key,
        note_id=note_id,
    )


def abandon_intent(
    *,
    forge_attempt_id: str,
    reason: str,
    anki: AnkiGateway,
    event_log: EventLogPort,
) -> RepairResult:
    """Explicitly close a zero-note pending intent without inventing success."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be non-empty")
    state = _state_for(event_log, forge_attempt_id)
    if state.acceptance is not None or state.abandoned is not None:
        return RepairResult(
            status=RepairStatus.ALREADY_RESOLVED,
            forge_attempt_id=forge_attempt_id,
            unit_key=state.unit_key,
        )

    note_ids = tuple(anki.find_notes(unit_key_query(state.unit_key)))
    if note_ids:
        return RepairResult(
            status=RepairStatus.AMBIGUOUS,
            forge_attempt_id=forge_attempt_id,
            unit_key=state.unit_key,
            note_id=note_ids[0] if len(note_ids) == 1 else None,
            ambiguous_note_ids=note_ids if len(note_ids) > 1 else (),
        )

    event_log.log(
        "FORGE",
        state.unit_key,
        abandoned_payload(
            source_ref=state.intent.payload["source_ref"],
            attempt_id=forge_attempt_id,
            reason=reason,
        ),
    )
    return RepairResult(
        status=RepairStatus.ABANDONED,
        forge_attempt_id=forge_attempt_id,
        unit_key=state.unit_key,
    )
