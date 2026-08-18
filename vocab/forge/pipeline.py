"""T6 Forge orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from ..validators import validate_forge_unit
from .build import (
    build_candidate,
    build_preview,
    generation_request_sha256,
    identity_trusted,
    structured_output_sha256,
    unit_key_query,
    validate_attempt_id,
    validate_generation_metadata,
    validate_preflight,
)
from .event_payloads import (
    acceptance_payload,
    analyze_attempts,
    commit_intent_payload,
    pending_for_unit,
    provenance_payload,
    rejection_payload,
    uncertain_payload,
)
from .ports import AnkiGateway, ConfirmationPort, EventLogPort, Generator
from .request import (
    ConfirmationDecision,
    ForgeRequest,
    ForgeResult,
    ForgeStatus,
    GenerationMetadata,
)
from .schema import FORGE_JSON_SCHEMA, ForgeSchemaError, parse_forge_output


def _aborted(outcome: str, *, violations: tuple[str, ...] = ()) -> ForgeResult:
    return ForgeResult(status=ForgeStatus.ABORTED, outcome=outcome, violations=violations)


def _read_attempts(event_log: EventLogPort):
    return analyze_attempts(event_log.read(event_type="FORGE"))


def _new_attempt_id(factory: Callable[[], str], existing: Mapping[str, object]) -> str:
    attempt_id = validate_attempt_id(factory())
    if attempt_id in existing:
        raise ValueError(f"forge_attempt_id already exists: {attempt_id!r}")
    return attempt_id


def _producer_justification_ok(output: Mapping[str, Any]) -> bool:
    justification = output["target_justification"]
    assert isinstance(justification, Mapping)
    expected = {
        channel
        for channel in ("W", "S")
        if output[f"target_{channel}"] is True
    }
    actual = set(justification)
    if actual != expected:
        return False
    return all(
        isinstance(justification[channel], str) and justification[channel].strip()
        for channel in expected
    )


def _emit_rejection(
    *,
    unit_key: str,
    source_ref: str,
    outcome: str,
    phase: str,
    violations: tuple[str, ...],
    structured_output: Mapping[str, Any],
    request_hash: str,
    output_hash: str,
    metadata: GenerationMetadata,
    event_log: EventLogPort,
    attempt_id_factory: Callable[[], str],
    decided_by: str = "",
    duplicate_note_ids: tuple[int, ...] = (),
) -> ForgeResult:
    try:
        states = _read_attempts(event_log)
        attempt_id = _new_attempt_id(attempt_id_factory, states)
        provenance = provenance_payload(
            attempt_id=attempt_id,
            metadata=metadata,
            generation_request_sha256=request_hash,
            structured_output_sha256=output_hash,
            structured_output=structured_output,
        )
        event_log.log(
            "FORGE",
            unit_key,
            rejection_payload(
                source_ref=source_ref,
                outcome=outcome,
                phase=phase,
                attempt_id=attempt_id,
                provenance=provenance,
                violations=violations,
                decided_by=decided_by,
                duplicate_note_ids=duplicate_note_ids,
            ),
        )
    except Exception:
        return _aborted("EVENTLOG_UNAVAILABLE")
    return ForgeResult(
        status=ForgeStatus.REJECTED,
        unit_key=unit_key,
        forge_attempt_id=attempt_id,
        outcome=outcome,
        violations=violations,
        ambiguous_note_ids=duplicate_note_ids if len(duplicate_note_ids) > 1 else (),
    )


def forge(
    request: ForgeRequest,
    *,
    deck_name: str,
    generator: Generator,
    anki: AnkiGateway,
    event_log: EventLogPort,
    confirmation: ConfirmationPort,
    generation_metadata: GenerationMetadata,
    today: Callable[[], date],
    attempt_id_factory: Callable[[], str],
) -> ForgeResult:
    """Run one fail-closed T6 Forge attempt."""
    validate_generation_metadata(generation_metadata)
    if not validate_preflight(request, deck_name):
        return _aborted("REQUEST_INVALID")

    request_hash = generation_request_sha256(request)
    try:
        raw_output = generator.generate(
            request,
            json_schema=FORGE_JSON_SCHEMA,
            metadata=generation_metadata,
        )
    except Exception:
        return _aborted("GENERATION_FAILED")

    try:
        output = parse_forge_output(raw_output)
    except (ForgeSchemaError, TypeError, ValueError):
        return _aborted("SCHEMA_INVALID")

    current_day = today()
    if not isinstance(current_day, date):
        raise TypeError("today() must return datetime.date")
    unit = build_candidate(request, output, today=current_day)
    violations = validate_forge_unit(unit)

    if not identity_trusted(unit):
        return _aborted("IDENTITY_INVALID", violations=violations)

    output_hash = structured_output_sha256(output)
    if violations:
        return _emit_rejection(
            unit_key=unit.unit_key,
            source_ref=unit.source_ref,
            outcome="VALIDATOR_REJECTED",
            phase="validate",
            violations=violations,
            structured_output=output,
            request_hash=request_hash,
            output_hash=output_hash,
            metadata=generation_metadata,
            event_log=event_log,
            attempt_id_factory=attempt_id_factory,
        )

    if not _producer_justification_ok(output):
        return _emit_rejection(
            unit_key=unit.unit_key,
            source_ref=unit.source_ref,
            outcome="JUSTIFICATION_MISSING",
            phase="justification",
            violations=(),
            structured_output=output,
            request_hash=request_hash,
            output_hash=output_hash,
            metadata=generation_metadata,
            event_log=event_log,
            attempt_id_factory=attempt_id_factory,
        )

    try:
        states = _read_attempts(event_log)
    except Exception:
        return _aborted("EVENTLOG_UNAVAILABLE")

    pending = pending_for_unit(states, unit.unit_key)
    if pending:
        if len(pending) > 1:
            return ForgeResult(
                status=ForgeStatus.COMMIT_UNCERTAIN,
                unit_key=unit.unit_key,
                outcome="PENDING_INTENT_AMBIGUOUS",
            )
        state = pending[0]
        try:
            note_ids = tuple(anki.find_notes(unit_key_query(unit.unit_key)))
        except Exception:
            return _aborted("ANKI_READ_FAILED")
        if len(note_ids) == 1:
            return ForgeResult(
                status=ForgeStatus.EVIDENCE_GAP,
                unit_key=unit.unit_key,
                forge_attempt_id=state.attempt_id,
                note_id=note_ids[0],
                outcome="PENDING_INTENT",
            )
        return ForgeResult(
            status=ForgeStatus.COMMIT_UNCERTAIN,
            unit_key=unit.unit_key,
            forge_attempt_id=state.attempt_id,
            outcome=("PENDING_INTENT" if not note_ids else "PENDING_INTENT_AMBIGUOUS"),
            ambiguous_note_ids=note_ids if len(note_ids) > 1 else (),
        )

    try:
        note_ids = tuple(anki.find_notes(unit_key_query(unit.unit_key)))
    except Exception:
        return _aborted("ANKI_READ_FAILED")
    if note_ids:
        return _emit_rejection(
            unit_key=unit.unit_key,
            source_ref=unit.source_ref,
            outcome="DUPLICATE",
            phase="dedup",
            violations=(),
            structured_output=output,
            request_hash=request_hash,
            output_hash=output_hash,
            metadata=generation_metadata,
            event_log=event_log,
            attempt_id_factory=attempt_id_factory,
            duplicate_note_ids=note_ids,
        )

    preview = build_preview(unit, output["target_justification"])
    decision = confirmation.decide(preview)
    if not isinstance(decision, ConfirmationDecision):
        raise TypeError("ConfirmationPort.decide() must return ConfirmationDecision")
    if not isinstance(decision.actor_id, str) or not decision.actor_id.strip():
        raise ValueError("confirmation actor_id must be non-empty")
    if not decision.confirmed:
        return _emit_rejection(
            unit_key=unit.unit_key,
            source_ref=unit.source_ref,
            outcome="HUMAN_DECLINED",
            phase="confirm",
            violations=(),
            structured_output=output,
            request_hash=request_hash,
            output_hash=output_hash,
            metadata=generation_metadata,
            event_log=event_log,
            attempt_id_factory=attempt_id_factory,
            decided_by=decision.actor_id,
        )

    try:
        attempt_id = _new_attempt_id(attempt_id_factory, states)
    except (TypeError, ValueError):
        return _aborted("ATTEMPT_ID_INVALID")
    provenance = provenance_payload(
        attempt_id=attempt_id,
        metadata=generation_metadata,
        generation_request_sha256=request_hash,
        structured_output_sha256=output_hash,
        structured_output=output,
    )
    try:
        event_log.log(
            "FORGE",
            unit.unit_key,
            commit_intent_payload(
                source_ref=unit.source_ref,
                attempt_id=attempt_id,
                confirmed_by=decision.actor_id,
                provenance=provenance,
            ),
        )
    except Exception:
        return _aborted("EVENTLOG_UNAVAILABLE")

    try:
        created_ids = anki.add_notes(deck_name, [unit])
        if (
            not isinstance(created_ids, list)
            or len(created_ids) != 1
            or type(created_ids[0]) is not int
        ):
            raise ValueError("add_notes did not prove exactly one note creation")
    except Exception as exc:
        try:
            event_log.log(
                "FORGE",
                unit.unit_key,
                uncertain_payload(
                    source_ref=unit.source_ref,
                    attempt_id=attempt_id,
                    error_kind=type(exc).__name__,
                ),
            )
        except Exception:
            pass
        return ForgeResult(
            status=ForgeStatus.COMMIT_UNCERTAIN,
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
            outcome="ANKI_COMMIT_UNCERTAIN",
        )

    note_id = created_ids[0]
    try:
        event_log.log(
            "FORGE",
            unit.unit_key,
            acceptance_payload(
                source_ref=unit.source_ref,
                attempt_id=attempt_id,
                note_id=note_id,
                structured_output_sha256=output_hash,
            ),
        )
    except Exception:
        return ForgeResult(
            status=ForgeStatus.EVIDENCE_GAP,
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
            note_id=note_id,
            outcome="ACCEPTANCE_UNWRITABLE",
        )

    return ForgeResult(
        status=ForgeStatus.CREATED,
        unit_key=unit.unit_key,
        forge_attempt_id=attempt_id,
        note_id=note_id,
    )
