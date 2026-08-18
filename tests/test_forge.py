"""Focused tests for the provider-neutral T6 Forge pipeline."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import vocab.forge.pipeline as pipeline_module
from vocab.forge import (
    FORGE_JSON_SCHEMA,
    ConfirmationDecision,
    ForgePreview,
    ForgeRequest,
    ForgeStatus,
    GenerationMetadata,
    RepairStatus,
    abandon_intent,
    forge,
    repair_evidence,
)
from vocab.forge.event_payloads import canonical_json_bytes, canonical_sha256
from vocab.models import Event, VocabUnit


UNIT_KEY = "subtle::small-difference"
ATTEMPT_ID = "attempt-123"


def valid_request(**overrides: object) -> ForgeRequest:
    values: dict[str, object] = {
        "source_ref": "dictionary:cambridge:subtle",
        "source_sentence": "The difference between the two shades is subtle.",
        "learner_note": "contrast the close meanings",
    }
    values.update(overrides)
    return ForgeRequest(**values)


def valid_metadata(**overrides: object) -> GenerationMetadata:
    values: dict[str, object] = {
        "model_id": "test-model",
        "model_version": "2026-08",
        "prompt_version": "forge-prompt-v1",
        "prompt_sha256": "a" * 64,
        "generation_config": {"temperature": 0.0, "seed": 7},
    }
    values.update(overrides)
    return GenerationMetadata(**values)


def valid_output(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "lemma": "subtle",
        "lemma_slug": "subtle",
        "sense_slug": "small-difference",
        "unit_type": "word",
        "register": "neutral",
        "definition_en": "not obvious and therefore difficult to notice",
        "target_R": True,
        "target_L": False,
        "target_W": False,
        "target_S": False,
        "target_justification": {},
    }
    values.update(overrides)
    return values


class FakeGenerator:
    def __init__(self, output: object | None = None, error: Exception | None = None):
        self.output = valid_output() if output is None else output
        self.error = error
        self.calls: list[tuple[ForgeRequest, dict[str, object], GenerationMetadata]] = []
        self.raw_provider_response = "provider-secret-envelope"

    def generate(self, request, *, json_schema, metadata):
        self.calls.append((request, json_schema, metadata))
        if self.error is not None:
            raise self.error
        return self.output


class FakeAnki:
    def __init__(
        self,
        *,
        find_result: object = None,
        add_result: object = None,
        find_error: Exception | None = None,
        add_error: Exception | None = None,
        trace: list[str] | None = None,
    ) -> None:
        self.find_result = [] if find_result is None else find_result
        self.add_result = [501] if add_result is None else add_result
        self.find_error = find_error
        self.add_error = add_error
        self.find_calls: list[str] = []
        self.add_calls: list[tuple[str, list[VocabUnit]]] = []
        self.trace = trace

    def find_notes(self, query: str) -> list[int]:
        self.find_calls.append(query)
        if self.trace is not None:
            self.trace.append("find_notes")
        if self.find_error is not None:
            raise self.find_error
        return self.find_result

    def add_notes(self, deck_name: str, units) -> list[int]:
        copied = list(units)
        self.add_calls.append((deck_name, copied))
        if self.trace is not None:
            self.trace.append("add_notes")
        if self.add_error is not None:
            raise self.add_error
        return self.add_result


class FakeEventLog:
    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        read_error: Exception | None = None,
        fail_outcomes: set[object] | None = None,
        trace: list[str] | None = None,
    ) -> None:
        self.events = list(events or [])
        self.read_error = read_error
        self.fail_outcomes = fail_outcomes or set()
        self.log_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.read_calls: list[tuple[str | None, str | None]] = []
        self.trace = trace

    def log(self, event: str, unit_key: str, payload: dict[str, Any]) -> Event:
        payload_copy = dict(payload)
        self.log_calls.append((event, unit_key, payload_copy))
        outcome_key = (
            "ACCEPTANCE" if payload_copy.get("accepted") is True else payload_copy.get("outcome")
        )
        if self.trace is not None:
            self.trace.append(f"log:{outcome_key}")
        if outcome_key in self.fail_outcomes:
            raise OSError("event log unavailable")
        stored = make_event(unit_key=unit_key, payload=payload_copy)
        self.events.append(stored)
        return stored

    def read(self, event_type=None, since=None) -> list[Event]:
        self.read_calls.append((event_type, since))
        if self.read_error is not None:
            raise self.read_error
        return [
            event
            for event in self.events
            if event_type is None or event.event == event_type
        ]


class FakeConfirmation:
    def __init__(
        self,
        decision: object = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.decision = (
            ConfirmationDecision(True, "curator-1")
            if decision is None
            else decision
        )
        self.error = error
        self.previews: list[object] = []

    def decide(self, preview: ForgePreview) -> ConfirmationDecision:
        self.previews.append(preview)
        if self.error is not None:
            raise self.error
        return self.decision


class AttemptFactory:
    def __init__(self, value: object = ATTEMPT_ID) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def make_event(
    *,
    unit_key: str = UNIT_KEY,
    payload: dict[str, Any],
) -> Event:
    return Event(
        v=1,
        ts="2026-08-18T00:00:00+00:00",
        day="2026-08-18",
        event="FORGE",
        unit_key=unit_key,
        payload=dict(payload),
    )


def intent_event(
    attempt_id: str = ATTEMPT_ID,
    *,
    unit_key: str = UNIT_KEY,
) -> Event:
    return make_event(
        unit_key=unit_key,
        payload={
            "source_ref": "dictionary:cambridge:subtle",
            "accepted": False,
            "outcome": "COMMIT_INTENT",
            "forge_attempt_id": attempt_id,
            "structured_output_sha256": "b" * 64,
        },
    )


def acceptance_event(attempt_id: str = ATTEMPT_ID) -> Event:
    return make_event(
        payload={
            "source_ref": "dictionary:cambridge:subtle",
            "accepted": True,
            "forge_attempt_id": attempt_id,
            "note_id": 501,
            "structured_output_sha256": "b" * 64,
        }
    )


def uncertain_event(attempt_id: str = ATTEMPT_ID) -> Event:
    return make_event(
        payload={
            "source_ref": "dictionary:cambridge:subtle",
            "accepted": False,
            "outcome": "ANKI_COMMIT_UNCERTAIN",
            "forge_attempt_id": attempt_id,
            "error_kind": "TimeoutError",
        }
    )


def run_forge(**overrides: object):
    values: dict[str, object] = {
        "request": valid_request(),
        "deck_name": "Vocabulary",
        "generator": FakeGenerator(),
        "anki": FakeAnki(),
        "event_log": FakeEventLog(),
        "confirmation": FakeConfirmation(),
        "generation_metadata": valid_metadata(),
        "today": lambda: date(2026, 8, 18),
        "attempt_id_factory": AttemptFactory(),
    }
    values.update(overrides)
    return forge(**values), values


@pytest.mark.parametrize(
    ("deck_name", "request_value"),
    [
        ("", valid_request()),
        ("   ", valid_request()),
        (7, valid_request()),
        ("Vocabulary", valid_request(source_ref="web:bad:source")),
        ("Vocabulary", valid_request(source_sentence="   ")),
        ("Vocabulary", valid_request(learner_note=3)),
    ],
)
def test_preflight_rejects_invalid_request_before_any_port(
    deck_name, request_value
) -> None:
    generator = FakeGenerator()
    anki = FakeAnki()
    event_log = FakeEventLog()

    result, _ = run_forge(
        deck_name=deck_name,
        request=request_value,
        generator=generator,
        anki=anki,
        event_log=event_log,
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "REQUEST_INVALID",
    )
    assert generator.calls == []
    assert event_log.read_calls == []
    assert event_log.log_calls == []
    assert anki.find_calls == []


@pytest.mark.parametrize(
    "metadata",
    [
        valid_metadata(prompt_sha256="A" * 64),
        valid_metadata(prompt_sha256="a" * 63),
        valid_metadata(model_id=" "),
        valid_metadata(generation_config={"temperature": float("nan")}),
        valid_metadata(generation_config={"temperature": float("inf")}),
        valid_metadata(generation_config={"nested": {"bad": True}}),
        valid_metadata(generation_config={1: "bad-key"}),
    ],
)
def test_invalid_generation_metadata_fails_before_generation(metadata) -> None:
    generator = FakeGenerator()

    result, _ = run_forge(generator=generator, generation_metadata=metadata)

    assert result.outcome == "REQUEST_INVALID"
    assert generator.calls == []


def test_generator_receives_strict_schema_once_and_no_retry_on_failure() -> None:
    generator = FakeGenerator(error=TimeoutError("provider timed out"))

    result, _ = run_forge(generator=generator)

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "GENERATION_FAILED",
    )
    assert len(generator.calls) == 1
    assert generator.calls[0][1]["additionalProperties"] is False
    assert generator.calls[0][1] == FORGE_JSON_SCHEMA


@pytest.mark.parametrize(
    "output",
    [
        [],
        {**valid_output(), "unit_key": UNIT_KEY},
        {**valid_output(), "source_ref": "dictionary:bad:injected"},
        {**valid_output(), "Target_R": "1"},
        {**valid_output(), "state_R": "NEW"},
        {**valid_output(), "created": "2026-08-18"},
        {key: value for key, value in valid_output().items() if key != "lemma"},
        valid_output(target_R=1),
        valid_output(target_R="true"),
        valid_output(target_justification={"W": "reason", "X": "extra"}),
    ],
)
def test_strict_schema_rejects_missing_extra_and_nonboolean_output(output) -> None:
    event_log = FakeEventLog()
    anki = FakeAnki()

    result, _ = run_forge(
        generator=FakeGenerator(output), event_log=event_log, anki=anki
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "SCHEMA_INVALID",
    )
    assert event_log.read_calls == []
    assert event_log.log_calls == []
    assert anki.find_calls == []


def test_build_composes_exact_key_maps_targets_and_leaves_later_fields_empty() -> None:
    anki = FakeAnki()
    output = valid_output(target_L=True, target_W=True, target_justification={"W": "write it"})

    result, _ = run_forge(generator=FakeGenerator(output), anki=anki)

    assert result.status is ForgeStatus.CREATED
    _, units = anki.add_calls[0]
    unit = units[0]
    assert unit.unit_key == "subtle::small-difference"
    assert (unit.Target_R, unit.Target_L, unit.Target_W, unit.Target_S) == (
        "1",
        "1",
        "1",
        "",
    )
    assert (unit.state_R, unit.state_L, unit.state_W, unit.state_S) == (
        "NEW",
        "NEW",
        "NEW",
        "",
    )
    assert tuple(unit.context_fields().values()) == ("",) * 5
    assert tuple(unit.audio_fields().values()) == ("",) * 3
    assert unit.VisualCue == unit.freq_band == unit.graduated == ""
    assert unit.created == "2026-08-18"


def test_invalid_slug_is_not_repaired_and_identity_aborts_after_validator(
    monkeypatch,
) -> None:
    calls: list[VocabUnit] = []
    real_validator = pipeline_module.validate_forge_unit

    def recording_validator(unit: VocabUnit) -> tuple[str, ...]:
        calls.append(unit)
        return real_validator(unit)

    monkeypatch.setattr(pipeline_module, "validate_forge_unit", recording_validator)
    event_log = FakeEventLog()
    attempts = AttemptFactory()

    result, _ = run_forge(
        generator=FakeGenerator(valid_output(lemma_slug="Bad Slug")),
        event_log=event_log,
        attempt_id_factory=attempts,
    )

    assert len(calls) == 1
    assert calls[0].unit_key == "Bad Slug::small-difference"
    assert result.status is ForgeStatus.ABORTED
    assert result.outcome == "IDENTITY_INVALID"
    assert result.unit_key == ""
    assert result.violations[:2] == (
        "F_LEMMA_SLUG_INVALID",
        "F_UNIT_KEY_INVALID",
    )
    assert attempts.calls == 0
    assert event_log.log_calls == []


def test_identity_valid_validator_rejection_preserves_code_order_and_output() -> None:
    output = valid_output(definition_en=" ", target_R=False)
    event_log = FakeEventLog()

    result, _ = run_forge(generator=FakeGenerator(output), event_log=event_log)

    assert result.status is ForgeStatus.REJECTED
    assert result.outcome == "VALIDATOR_REJECTED"
    assert result.violations == ("F_NO_TARGET_ENABLED", "F_DEFINITION_EMPTY")
    payload = event_log.log_calls[0][2]
    assert payload["violations"] == list(result.violations)
    assert payload["structured_output"] == output


@pytest.mark.parametrize(
    "output",
    [
        valid_output(target_W=True),
        valid_output(target_W=True, target_justification={"W": "   "}),
        valid_output(target_S=True),
        valid_output(target_justification={"W": "disabled key is forbidden"}),
        valid_output(target_justification={"S": "disabled key is forbidden"}),
    ],
)
def test_productive_justification_is_required_exactly_when_enabled(output) -> None:
    result, values = run_forge(generator=FakeGenerator(output))

    assert (result.status, result.outcome) == (
        ForgeStatus.REJECTED,
        "JUSTIFICATION_MISSING",
    )
    assert values["anki"].find_calls == []


def test_valid_productive_justification_is_preserved_without_trimming() -> None:
    reason = "  Useful in formal writing.  "
    event_log = FakeEventLog()

    result, _ = run_forge(
        generator=FakeGenerator(
            valid_output(target_W=True, target_justification={"W": reason})
        ),
        event_log=event_log,
    )

    assert result.status is ForgeStatus.CREATED
    assert event_log.log_calls[0][2]["target_justification"] == {"W": reason}


@pytest.mark.parametrize("attempt_id", ["short", "bad id!!", "x" * 129, 17])
def test_invalid_attempt_id_fails_closed_without_event(attempt_id) -> None:
    event_log = FakeEventLog()
    attempts = AttemptFactory(attempt_id)

    result, _ = run_forge(event_log=event_log, attempt_id_factory=attempts)

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "ATTEMPT_ID_INVALID",
    )
    assert attempts.calls == 1
    assert event_log.log_calls == []


def test_one_attempt_id_is_reused_for_intent_and_acceptance() -> None:
    event_log = FakeEventLog()
    attempts = AttemptFactory()

    result, _ = run_forge(event_log=event_log, attempt_id_factory=attempts)

    assert result.status is ForgeStatus.CREATED
    assert attempts.calls == 1
    assert [call[2]["forge_attempt_id"] for call in event_log.log_calls] == [
        ATTEMPT_ID,
        ATTEMPT_ID,
    ]


@pytest.mark.parametrize(
    ("note_ids", "status", "outcome"),
    [
        ([], ForgeStatus.COMMIT_UNCERTAIN, "PENDING_INTENT"),
        ([501], ForgeStatus.EVIDENCE_GAP, "PENDING_INTENT"),
        ([501, 502], ForgeStatus.COMMIT_UNCERTAIN, "PENDING_INTENT_AMBIGUOUS"),
    ],
)
def test_pending_intent_guard_reconciles_zero_one_or_many_notes(
    note_ids, status, outcome
) -> None:
    event_log = FakeEventLog([intent_event()])
    anki = FakeAnki(find_result=note_ids)
    attempts = AttemptFactory()

    result, _ = run_forge(
        event_log=event_log,
        anki=anki,
        attempt_id_factory=attempts,
    )

    assert (result.status, result.outcome) == (status, outcome)
    assert result.forge_attempt_id == ATTEMPT_ID
    assert result.note_id == (501 if len(note_ids) == 1 else None)
    assert result.ambiguous_note_ids == (
        tuple(note_ids) if len(note_ids) > 1 else ()
    )
    assert attempts.calls == 0
    assert anki.add_calls == []
    assert event_log.log_calls == []


def test_multiple_pending_intents_fail_closed_without_anki_or_new_attempt() -> None:
    event_log = FakeEventLog(
        [intent_event("attempt-111"), intent_event("attempt-222")]
    )
    anki = FakeAnki()
    attempts = AttemptFactory()

    result, _ = run_forge(
        event_log=event_log,
        anki=anki,
        attempt_id_factory=attempts,
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.COMMIT_UNCERTAIN,
        "PENDING_INTENT_AMBIGUOUS",
    )
    assert anki.find_calls == []
    assert attempts.calls == 0


def test_uncertain_event_does_not_close_pending_intent() -> None:
    event_log = FakeEventLog([intent_event(), uncertain_event()])

    result, _ = run_forge(event_log=event_log, anki=FakeAnki(find_result=[]))

    assert (result.status, result.outcome) == (
        ForgeStatus.COMMIT_UNCERTAIN,
        "PENDING_INTENT",
    )


@pytest.mark.parametrize("note_ids", [[99], [99, 100]])
def test_dedup_rejects_without_writing_and_reports_note_ids(note_ids) -> None:
    event_log = FakeEventLog()
    anki = FakeAnki(find_result=note_ids)

    result, _ = run_forge(event_log=event_log, anki=anki)

    assert (result.status, result.outcome) == (
        ForgeStatus.REJECTED,
        "DUPLICATE",
    )
    assert anki.find_calls == [f"unit_key:{UNIT_KEY}"]
    assert anki.add_calls == []
    assert event_log.log_calls[0][2]["duplicate_note_ids"] == note_ids
    assert result.note_id == (99 if len(note_ids) == 1 else None)
    assert result.ambiguous_note_ids == (
        tuple(note_ids) if len(note_ids) > 1 else ()
    )


def test_anki_read_failure_aborts_without_attempt_or_event() -> None:
    attempts = AttemptFactory()
    event_log = FakeEventLog()

    result, _ = run_forge(
        anki=FakeAnki(find_error=OSError("offline")),
        event_log=event_log,
        attempt_id_factory=attempts,
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "ANKI_READ_FAILED",
    )
    assert attempts.calls == 0
    assert event_log.log_calls == []


def test_preview_is_frozen_and_contains_no_mutable_or_candidate_reference() -> None:
    confirmation = FakeConfirmation()

    result, _ = run_forge(confirmation=confirmation)

    assert result.status is ForgeStatus.CREATED
    preview = confirmation.previews[0]
    assert isinstance(preview, ForgePreview)
    assert not isinstance(preview, VocabUnit)
    assert preview.targets == ("R",)
    assert preview.states == (("R", "NEW"),)
    assert isinstance(preview.target_justification, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        preview.lemma = "changed"


@pytest.mark.parametrize(
    "decision",
    [
        ConfirmationDecision(True, ""),
        ConfirmationDecision(False, "  "),
        object(),
    ],
)
def test_confirmation_requires_a_valid_decision_and_actor(decision) -> None:
    event_log = FakeEventLog()
    anki = FakeAnki()

    result, _ = run_forge(
        confirmation=FakeConfirmation(decision), event_log=event_log, anki=anki
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "REQUEST_INVALID",
    )
    assert event_log.log_calls == []
    assert anki.add_calls == []


def test_human_decline_logs_actor_and_does_not_write_anki() -> None:
    event_log = FakeEventLog()
    anki = FakeAnki()

    result, _ = run_forge(
        confirmation=FakeConfirmation(ConfirmationDecision(False, "curator-x")),
        event_log=event_log,
        anki=anki,
    )

    assert (result.status, result.outcome) == (
        ForgeStatus.REJECTED,
        "HUMAN_DECLINED",
    )
    assert event_log.log_calls[0][2]["decided_by"] == "curator-x"
    assert anki.add_calls == []


def test_commit_intent_is_logged_before_one_anki_write() -> None:
    trace: list[str] = []
    event_log = FakeEventLog(trace=trace)
    anki = FakeAnki(trace=trace)

    result, _ = run_forge(event_log=event_log, anki=anki)

    assert result.status is ForgeStatus.CREATED
    assert trace == [
        "find_notes",
        "log:COMMIT_INTENT",
        "add_notes",
        "log:ACCEPTANCE",
    ]
    assert len(anki.add_calls) == 1
    assert "note_id" not in event_log.log_calls[0][2]


def test_commit_intent_failure_blocks_anki_write() -> None:
    event_log = FakeEventLog(fail_outcomes={"COMMIT_INTENT"})
    anki = FakeAnki()

    result, _ = run_forge(event_log=event_log, anki=anki)

    assert (result.status, result.outcome) == (
        ForgeStatus.ABORTED,
        "EVENTLOG_UNAVAILABLE",
    )
    assert anki.add_calls == []


@pytest.mark.parametrize(
    ("add_result", "add_error", "error_kind"),
    [
        (None, TimeoutError("unknown commit"), "TimeoutError"),
        ([], None, "MALFORMED_RESPONSE"),
        ([501, 502], None, "MALFORMED_RESPONSE"),
        ([True], None, "MALFORMED_RESPONSE"),
    ],
)
def test_uncertain_anki_commit_is_not_retried_or_accepted(
    add_result, add_error, error_kind
) -> None:
    event_log = FakeEventLog()
    anki = FakeAnki(add_result=add_result, add_error=add_error)

    result, _ = run_forge(event_log=event_log, anki=anki)

    assert (result.status, result.outcome) == (
        ForgeStatus.COMMIT_UNCERTAIN,
        "ANKI_COMMIT_UNCERTAIN",
    )
    assert len(anki.add_calls) == 1
    assert [call[2].get("outcome") for call in event_log.log_calls] == [
        "COMMIT_INTENT",
        "ANKI_COMMIT_UNCERTAIN",
    ]
    assert event_log.log_calls[1][2]["error_kind"] == error_kind


def test_acceptance_failure_leaves_note_and_reports_evidence_gap() -> None:
    event_log = FakeEventLog(fail_outcomes={"ACCEPTANCE"})
    anki = FakeAnki(add_result=[707])

    result, _ = run_forge(event_log=event_log, anki=anki)

    assert (result.status, result.outcome, result.note_id) == (
        ForgeStatus.EVIDENCE_GAP,
        "ACCEPTANCE_UNWRITABLE",
        707,
    )
    assert len(anki.add_calls) == 1


def test_event_payload_contains_canonical_provenance_not_provider_envelope() -> None:
    generator = FakeGenerator()
    event_log = FakeEventLog()
    request = valid_request()

    result, _ = run_forge(
        request=request, generator=generator, event_log=event_log
    )

    assert result.status is ForgeStatus.CREATED
    intent = event_log.log_calls[0][2]
    expected_request = {
        "source_ref": request.source_ref,
        "source_sentence": request.source_sentence,
        "learner_note": request.learner_note,
    }
    assert intent["generation_request_sha256"] == canonical_sha256(expected_request)
    assert intent["structured_output_sha256"] == canonical_sha256(valid_output())
    assert intent["structured_output"] == valid_output()
    assert "provider-secret-envelope" not in repr(intent)
    assert event_log.log_calls[1][2] == {
        "source_ref": request.source_ref,
        "accepted": True,
        "forge_attempt_id": ATTEMPT_ID,
        "note_id": 501,
        "structured_output_sha256": canonical_sha256(valid_output()),
    }


def test_request_hash_changes_when_only_learner_note_changes() -> None:
    logs: list[FakeEventLog] = []
    hashes: list[str] = []
    for note in ("first", "second"):
        event_log = FakeEventLog()
        logs.append(event_log)
        result, _ = run_forge(
            request=valid_request(learner_note=note), event_log=event_log
        )
        assert result.status is ForgeStatus.CREATED
        hashes.append(event_log.log_calls[0][2]["generation_request_sha256"])

    assert hashes[0] != hashes[1]


def test_canonical_json_is_sorted_compact_utf8_and_rejects_nan() -> None:
    assert canonical_json_bytes({"z": 1, "a": "tiếng Việt"}) == (
        '{"a":"tiếng Việt","z":1}'.encode("utf-8")
    )
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})


@pytest.mark.parametrize(
    ("note_ids", "status"),
    [
        ([], RepairStatus.NO_NOTE),
        ([701], RepairStatus.REPAIRED),
        ([701, 702], RepairStatus.AMBIGUOUS),
    ],
)
def test_repair_evidence_handles_zero_one_or_many_notes(note_ids, status) -> None:
    event_log = FakeEventLog([intent_event()])

    result = repair_evidence(
        forge_attempt_id=ATTEMPT_ID,
        anki=FakeAnki(find_result=note_ids),
        event_log=event_log,
    )

    assert result.status is status
    if status is RepairStatus.REPAIRED:
        repaired = event_log.log_calls[0][2]
        assert repaired == {
            "source_ref": "dictionary:cambridge:subtle",
            "accepted": True,
            "forge_attempt_id": ATTEMPT_ID,
            "note_id": 701,
            "structured_output_sha256": "b" * 64,
            "repaired": True,
            "repair_reason": "recovered-from-commit-intent",
        }
    else:
        assert event_log.log_calls == []


def test_repair_fails_closed_for_missing_duplicate_or_resolved_intent() -> None:
    cases = [
        ([], RepairStatus.AMBIGUOUS),
        ([intent_event(), intent_event()], RepairStatus.AMBIGUOUS),
        ([intent_event(), acceptance_event()], RepairStatus.ALREADY_RESOLVED),
    ]
    for events, expected in cases:
        anki = FakeAnki(find_result=[701])
        event_log = FakeEventLog(events)
        result = repair_evidence(
            forge_attempt_id=ATTEMPT_ID,
            anki=anki,
            event_log=event_log,
        )
        assert result.status is expected
        assert event_log.log_calls == []
        assert anki.find_calls == []


def test_uncertain_event_does_not_prevent_repair() -> None:
    event_log = FakeEventLog([intent_event(), uncertain_event()])

    result = repair_evidence(
        forge_attempt_id=ATTEMPT_ID,
        anki=FakeAnki(find_result=[808]),
        event_log=event_log,
    )

    assert result.status is RepairStatus.REPAIRED
    assert result.note_id == 808


@pytest.mark.parametrize(
    ("note_ids", "expected"),
    [
        ([], RepairStatus.ABANDONED),
        ([801], RepairStatus.AMBIGUOUS),
        ([801, 802], RepairStatus.AMBIGUOUS),
    ],
)
def test_abandonment_requires_zero_anki_notes(note_ids, expected) -> None:
    event_log = FakeEventLog([intent_event()])

    result = abandon_intent(
        forge_attempt_id=ATTEMPT_ID,
        reason="operator verified no note was created",
        anki=FakeAnki(find_result=note_ids),
        event_log=event_log,
    )

    assert result.status is expected
    if expected is RepairStatus.ABANDONED:
        assert event_log.log_calls[0][2]["outcome"] == "INTENT_ABANDONED"
        assert event_log.log_calls[0][2]["reason"] == (
            "operator verified no note was created"
        )
    else:
        assert event_log.log_calls == []


def test_abandonment_requires_nonempty_reason_and_pending_intent() -> None:
    anki = FakeAnki()
    event_log = FakeEventLog([intent_event()])

    invalid = abandon_intent(
        forge_attempt_id=ATTEMPT_ID,
        reason="  ",
        anki=anki,
        event_log=event_log,
    )
    assert invalid.status is RepairStatus.AMBIGUOUS
    assert anki.find_calls == []

    resolved_log = FakeEventLog([intent_event(), acceptance_event()])
    resolved = abandon_intent(
        forge_attempt_id=ATTEMPT_ID,
        reason="not needed",
        anki=anki,
        event_log=resolved_log,
    )
    assert resolved.status is RepairStatus.ALREADY_RESOLVED
    assert resolved_log.log_calls == []


def test_forge_package_has_no_provider_sdk_or_forbidden_anki_operations() -> None:
    forge_dir = Path(pipeline_module.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in forge_dir.glob("*.py")
    )

    assert "update_note_fields" not in source
    assert "delete_note" not in source
    assert "deleteNotes" not in source
    assert "openai" not in source.lower()
    assert "anthropic" not in source.lower()
    assert "sqlite" not in source.lower()
