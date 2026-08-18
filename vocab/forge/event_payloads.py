"""FORGE producer payloads and attempt-state analysis."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..models import Event
from .request import GenerationMetadata, PRODUCER_VERSION
from .schema import FORGE_SCHEMA_VERSION


def provenance_payload(
    *,
    attempt_id: str,
    metadata: GenerationMetadata,
    generation_request_sha256: str,
    structured_output_sha256: str,
    structured_output: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "forge_attempt_id": attempt_id,
        "model_id": metadata.model_id,
        "model_version": metadata.model_version,
        "prompt_version": metadata.prompt_version,
        "prompt_sha256": metadata.prompt_sha256,
        "forge_schema_version": FORGE_SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
        "generation_config": dict(metadata.generation_config),
        "generation_request_sha256": generation_request_sha256,
        "structured_output_sha256": structured_output_sha256,
        "structured_output": dict(structured_output),
    }


def rejection_payload(
    *,
    source_ref: str,
    outcome: str,
    phase: str,
    attempt_id: str,
    provenance: Mapping[str, Any],
    violations: Sequence[str] = (),
    decided_by: str = "",
    duplicate_note_ids: Sequence[int] = (),
) -> dict[str, Any]:
    payload = {
        "source_ref": source_ref,
        "accepted": False,
        "outcome": outcome,
        "phase": phase,
        "violations": list(violations),
        **dict(provenance),
    }
    payload["forge_attempt_id"] = attempt_id
    if decided_by:
        payload["decided_by"] = decided_by
    if duplicate_note_ids:
        payload["duplicate_note_ids"] = list(duplicate_note_ids)
    return payload


def commit_intent_payload(
    *,
    source_ref: str,
    attempt_id: str,
    confirmed_by: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "source_ref": source_ref,
        "accepted": False,
        "outcome": "COMMIT_INTENT",
        "confirmed_by": confirmed_by,
        **dict(provenance),
    }
    payload["forge_attempt_id"] = attempt_id
    return payload


def uncertain_payload(
    *,
    source_ref: str,
    attempt_id: str,
    error_kind: str,
) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "accepted": False,
        "outcome": "ANKI_COMMIT_UNCERTAIN",
        "forge_attempt_id": attempt_id,
        "error_kind": error_kind,
    }


def acceptance_payload(
    *,
    source_ref: str,
    attempt_id: str,
    note_id: int,
    structured_output_sha256: str,
    repaired: bool = False,
) -> dict[str, Any]:
    payload = {
        "source_ref": source_ref,
        "accepted": True,
        "forge_attempt_id": attempt_id,
        "note_id": note_id,
        "structured_output_sha256": structured_output_sha256,
    }
    if repaired:
        payload["repaired"] = True
        payload["repair_reason"] = "recovered-from-commit-intent"
    return payload


def abandoned_payload(
    *,
    source_ref: str,
    attempt_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "accepted": False,
        "outcome": "INTENT_ABANDONED",
        "forge_attempt_id": attempt_id,
        "reason": reason,
    }


@dataclass(frozen=True, slots=True)
class AttemptState:
    attempt_id: str
    unit_key: str
    intent: Event | None
    acceptance: Event | None
    abandoned: Event | None
    events: tuple[Event, ...]

    @property
    def pending(self) -> bool:
        return self.intent is not None and self.acceptance is None and self.abandoned is None


def analyze_attempts(events: Sequence[Event]) -> dict[str, AttemptState]:
    groups: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event != "FORGE":
            continue
        attempt_id = event.payload.get("forge_attempt_id")
        if isinstance(attempt_id, str) and attempt_id:
            groups[attempt_id].append(event)

    result: dict[str, AttemptState] = {}
    for attempt_id, group in groups.items():
        intents = [e for e in group if e.payload.get("outcome") == "COMMIT_INTENT"]
        accepted = [e for e in group if e.payload.get("accepted") is True]
        abandoned = [e for e in group if e.payload.get("outcome") == "INTENT_ABANDONED"]
        if len(intents) > 1 or len(accepted) > 1 or len(abandoned) > 1:
            raise ValueError(f"inconsistent FORGE attempt history for {attempt_id!r}")
        unit_keys = {e.unit_key for e in group}
        if len(unit_keys) != 1:
            raise ValueError(f"attempt {attempt_id!r} spans multiple unit_key values")
        result[attempt_id] = AttemptState(
            attempt_id=attempt_id,
            unit_key=next(iter(unit_keys)),
            intent=intents[0] if intents else None,
            acceptance=accepted[0] if accepted else None,
            abandoned=abandoned[0] if abandoned else None,
            events=tuple(group),
        )
    return result


def pending_for_unit(states: Mapping[str, AttemptState], unit_key: str) -> tuple[AttemptState, ...]:
    return tuple(state for state in states.values() if state.unit_key == unit_key and state.pending)
