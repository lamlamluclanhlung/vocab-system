"""Single-writer T6 Forge pipeline."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from datetime import date

from ..contracts import SOURCE_REF_PATTERN, UNIT_KEY_PATTERN, UNIT_KEY_SEPARATOR
from ..models import Event, VocabUnit
from ..validators import validate_forge_unit
from .build import build_preview, build_unit_key_query, build_vocab_unit
from .event_payloads import (
    ForgeProvenance,
    acceptance_payload,
    build_provenance,
    commit_intent_payload,
    is_valid_attempt_id,
    rejection_payload,
    validate_generation_metadata,
)
from .ports import AnkiGateway, ConfirmationPort, EventLogPort, Generator
from .recovery import (
    PendingStatus,
    inspect_pending_intent,
    read_forge_events,
)
from .request import (
    ConfirmationDecision,
    ForgeRequest,
    ForgeResult,
    ForgeStatus,
    GenerationMetadata,
)
from .schema import FORGE_JSON_SCHEMA, parse_strict_output


_SOURCE_REF_RE = re.compile(SOURCE_REF_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)


class _AttemptAllocator:
    """Lazily allocate one globally unused FORGE attempt ID."""

    def __init__(
        self,
        factory: Callable[[], str],
        event_log: EventLogPort,
    ) -> None:
        self._factory = factory
        self._event_log = event_log
        self._called = False
        self._value = ""
        self._failure_outcome = ""

    @property
    def failure_outcome(self) -> str:
        return self._failure_outcome or "ATTEMPT_ID_INVALID"

    def get(self) -> str | None:
        if self._called:
            return self._value or None
        self._called = True

        # Collision detection must consult durable history before an event can
        # reuse an old correlation ID. The factory remains lazy, so pre-identity
        # ABORTED outcomes still allocate no attempt ID.
        try:
            existing_events = read_forge_events(self._event_log)
        except Exception:
            self._failure_outcome = "EVENTLOG_UNAVAILABLE"
            return None

        try:
            candidate = self._factory()
        except Exception:
            self._failure_outcome = "ATTEMPT_ID_INVALID"
            return None
        if not is_valid_attempt_id(candidate):
            self._failure_outcome = "ATTEMPT_ID_INVALID"
            return None
        if any(
            event.payload.get("forge_attempt_id") == candidate
            for event in existing_events
        ):
            self._failure_outcome = "ATTEMPT_ID_INVALID"
            return None

        self._value = candidate
        return candidate


def _request_is_valid(request: object, deck_name: object) -> bool:
    return (
        isinstance(request, ForgeRequest)
        and isinstance(deck_name, str)
        and bool(deck_name.strip())
        and isinstance(request.source_ref, str)
        and _SOURCE_REF_RE.fullmatch(request.source_ref) is not None
        and isinstance(request.source_sentence, str)
        and bool(request.source_sentence.strip())
        and isinstance(request.learner_note, str)
    )


def _abort(
    outcome: str,
    *,
    unit_key: str = "",
    forge_attempt_id: str = "",
    violations: tuple[str, ...] = (),
) -> ForgeResult:
    return ForgeResult(
        ForgeStatus.ABORTED,
        unit_key=unit_key,
        forge_attempt_id=forge_attempt_id,
        outcome=outcome,
        violations=violations,
    )


def _log_event(
    event_log: EventLogPort,
    unit_key: str,
    payload: dict[str, object],
) -> bool:
    try:
        stored = event_log.log("FORGE", unit_key, payload)
    except Exception:
        return False
    return isinstance(stored, Event)


def _emit_rejection(
    *,
    unit: VocabUnit,
    request: ForgeRequest,
    provenance: ForgeProvenance,
    event_log: EventLogPort,
    attempts: _AttemptAllocator,
    outcome: str,
    violations: tuple[str, ...] = (),
    decided_by: str | None = None,
    duplicate_note_ids: tuple[int, ...] = (),
) -> ForgeResult:
    attempt_id = attempts.get()
    if attempt_id is None:
        return _abort(
            attempts.failure_outcome,
            unit_key=unit.unit_key,
            violations=violations,
        )
    payload = rejection_payload(
        request=request,
        forge_attempt_id=attempt_id,
        provenance=provenance,
        outcome=outcome,
        violations=violations,
        decided_by=decided_by,
        duplicate_note_ids=duplicate_note_ids,
    )
    if not _log_event(event_log, unit.unit_key, payload):
        return _abort(
            "EVENTLOG_UNAVAILABLE",
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
            violations=violations,
        )
    return ForgeResult(
        ForgeStatus.REJECTED,
        unit_key=unit.unit_key,
        forge_attempt_id=attempt_id,
        note_id=(duplicate_note_ids[0] if len(duplicate_note_ids) == 1 else None),
        outcome=outcome,
        violations=violations,
        ambiguous_note_ids=(
            duplicate_note_ids if len(duplicate_note_ids) > 1 else ()
        ),
    )


def _justification_is_valid(structured_output: dict[str, object]) -> bool:
    justification = structured_output["target_justification"]
    if not isinstance(justification, dict):
        return False
    for channel in ("W", "S"):
        enabled = structured_output[f"target_{channel}"] is True
        if enabled:
            if channel not in justification or not justification[channel].strip():
                return False
        elif channel in justification:
            return False
    return True


def _find_note_ids(anki: AnkiGateway, unit_key: str) -> tuple[int, ...]:
    result = anki.find_notes(build_unit_key_query(unit_key))
    if not isinstance(result, list) or any(type(note_id) is not int for note_id in result):
        raise ValueError("Anki returned malformed note IDs")
    return tuple(result)


def _uncertain_error_kind(error: BaseException | None) -> str:
    return "MALFORMED_RESPONSE" if error is None else type(error).__name__


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
    """Generate, validate, confirm, and durably commit one vocabulary Unit."""
    if not _request_is_valid(request, deck_name):
        return _abort("REQUEST_INVALID")
    try:
        metadata = validate_generation_metadata(generation_metadata)
    except Exception:
        return _abort("REQUEST_INVALID")

    generator_metadata = GenerationMetadata(
        model_id=metadata.model_id,
        model_version=metadata.model_version,
        prompt_version=metadata.prompt_version,
        prompt_sha256=metadata.prompt_sha256,
        generation_config=dict(metadata.generation_config),
    )
    try:
        generated = generator.generate(
            request,
            json_schema=copy.deepcopy(FORGE_JSON_SCHEMA),
            metadata=generator_metadata,
        )
    except Exception:
        return _abort("GENERATION_FAILED")

    try:
        structured_output = parse_strict_output(generated)
    except Exception:
        return _abort("SCHEMA_INVALID")

    try:
        created_on = today()
        unit = build_vocab_unit(
            structured_output,
            request,
            created_on=created_on,
        )
    except Exception:
        return _abort("REQUEST_INVALID")

    violations = validate_forge_unit(unit)
    identity_trusted = (
        _UNIT_KEY_RE.fullmatch(unit.unit_key) is not None
        and unit.unit_key
        == unit.lemma_slug + UNIT_KEY_SEPARATOR + unit.sense_slug
    )
    if not identity_trusted:
        return _abort("IDENTITY_INVALID", violations=violations)

    provenance = build_provenance(request, metadata, structured_output)
    attempts = _AttemptAllocator(attempt_id_factory, event_log)
    if violations:
        return _emit_rejection(
            unit=unit,
            request=request,
            provenance=provenance,
            event_log=event_log,
            attempts=attempts,
            outcome="VALIDATOR_REJECTED",
            violations=violations,
        )
    if not _justification_is_valid(structured_output):
        return _emit_rejection(
            unit=unit,
            request=request,
            provenance=provenance,
            event_log=event_log,
            attempts=attempts,
            outcome="JUSTIFICATION_MISSING",
        )

    try:
        forge_events = read_forge_events(event_log)
        pending = inspect_pending_intent(forge_events, unit.unit_key)
    except Exception:
        return _abort("EVENTLOG_UNAVAILABLE", unit_key=unit.unit_key)
    if pending.status is PendingStatus.AMBIGUOUS:
        return ForgeResult(
            ForgeStatus.COMMIT_UNCERTAIN,
            unit_key=unit.unit_key,
            outcome="PENDING_INTENT_AMBIGUOUS",
        )
    if pending.status is PendingStatus.ONE:
        try:
            note_ids = _find_note_ids(anki, unit.unit_key)
        except Exception:
            return _abort(
                "ANKI_READ_FAILED",
                unit_key=unit.unit_key,
                forge_attempt_id=pending.forge_attempt_id,
            )
        if not note_ids:
            return ForgeResult(
                ForgeStatus.COMMIT_UNCERTAIN,
                unit_key=unit.unit_key,
                forge_attempt_id=pending.forge_attempt_id,
                outcome="PENDING_INTENT",
            )
        if len(note_ids) == 1:
            return ForgeResult(
                ForgeStatus.EVIDENCE_GAP,
                unit_key=unit.unit_key,
                forge_attempt_id=pending.forge_attempt_id,
                note_id=note_ids[0],
                outcome="PENDING_INTENT",
            )
        return ForgeResult(
            ForgeStatus.COMMIT_UNCERTAIN,
            unit_key=unit.unit_key,
            forge_attempt_id=pending.forge_attempt_id,
            outcome="PENDING_INTENT_AMBIGUOUS",
            ambiguous_note_ids=note_ids,
        )

    try:
        duplicate_note_ids = _find_note_ids(anki, unit.unit_key)
    except Exception:
        return _abort("ANKI_READ_FAILED", unit_key=unit.unit_key)
    if duplicate_note_ids:
        return _emit_rejection(
            unit=unit,
            request=request,
            provenance=provenance,
            event_log=event_log,
            attempts=attempts,
            outcome="DUPLICATE",
            duplicate_note_ids=duplicate_note_ids,
        )

    justification = structured_output["target_justification"]
    preview = build_preview(unit, justification)
    try:
        decision = confirmation.decide(preview)
    except Exception:
        return _abort("REQUEST_INVALID", unit_key=unit.unit_key)
    if (
        not isinstance(decision, ConfirmationDecision)
        or type(decision.confirmed) is not bool
        or not isinstance(decision.actor_id, str)
        or not decision.actor_id.strip()
    ):
        return _abort("REQUEST_INVALID", unit_key=unit.unit_key)
    if not decision.confirmed:
        return _emit_rejection(
            unit=unit,
            request=request,
            provenance=provenance,
            event_log=event_log,
            attempts=attempts,
            outcome="HUMAN_DECLINED",
            decided_by=decision.actor_id,
        )

    attempt_id = attempts.get()
    if attempt_id is None:
        return _abort(attempts.failure_outcome, unit_key=unit.unit_key)
    intent = commit_intent_payload(
        request=request,
        forge_attempt_id=attempt_id,
        provenance=provenance,
        confirmed_by=decision.actor_id,
    )
    if not _log_event(event_log, unit.unit_key, intent):
        return _abort(
            "EVENTLOG_UNAVAILABLE",
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
        )

    error: BaseException | None = None
    try:
        add_result = anki.add_notes(deck_name, [unit])
    except Exception as exc:
        error = exc
        add_result = None
    if (
        not isinstance(add_result, list)
        or len(add_result) != 1
        or type(add_result[0]) is not int
    ):
        uncertain_payload = {
            "source_ref": request.source_ref,
            "accepted": False,
            "outcome": "ANKI_COMMIT_UNCERTAIN",
            "forge_attempt_id": attempt_id,
            "error_kind": _uncertain_error_kind(error),
        }
        _log_event(event_log, unit.unit_key, uncertain_payload)
        return ForgeResult(
            ForgeStatus.COMMIT_UNCERTAIN,
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
            outcome="ANKI_COMMIT_UNCERTAIN",
        )

    note_id = add_result[0]
    accepted_payload = acceptance_payload(
        source_ref=request.source_ref,
        forge_attempt_id=attempt_id,
        note_id=note_id,
        structured_output_sha256=provenance.structured_output_sha256,
    )
    if not _log_event(event_log, unit.unit_key, accepted_payload):
        return ForgeResult(
            ForgeStatus.EVIDENCE_GAP,
            unit_key=unit.unit_key,
            forge_attempt_id=attempt_id,
            note_id=note_id,
            outcome="ACCEPTANCE_UNWRITABLE",
        )
    return ForgeResult(
        ForgeStatus.CREATED,
        unit_key=unit.unit_key,
        forge_attempt_id=attempt_id,
        note_id=note_id,
    )
