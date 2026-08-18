from __future__ import annotations

from datetime import date

import pytest

from vocab.forge import (
    ConfirmationDecision,
    ForgeRequest,
    ForgeStatus,
    GenerationMetadata,
    RepairStatus,
    forge,
    repair_evidence,
)
from vocab.forge.build import generation_request_sha256
from vocab.models import Event


VALID_OUTPUT = {
    "lemma": "subtle",
    "lemma_slug": "subtle",
    "sense_slug": "small-difference",
    "unit_type": "word",
    "register": "neutral",
    "definition_en": "not obvious; delicate or precise",
    "target_R": True,
    "target_L": False,
    "target_W": False,
    "target_S": False,
    "target_justification": {},
}


class FakeGenerator:
    def __init__(self, output=None, exc: Exception | None = None):
        self.output = dict(VALID_OUTPUT if output is None else output)
        self.exc = exc
        self.calls = 0

    def generate(self, request, *, json_schema, metadata):
        self.calls += 1
        if self.exc:
            raise self.exc
        return dict(self.output)


class FakeAnki:
    def __init__(self, found=(), add_result=None, add_exc: Exception | None = None):
        self.found = list(found)
        self.add_result = [101] if add_result is None else add_result
        self.add_exc = add_exc
        self.add_calls = 0
        self.added_units = []

    def find_notes(self, query):
        return list(self.found)

    def add_notes(self, deck_name, units):
        self.add_calls += 1
        self.added_units.extend(units)
        if self.add_exc:
            raise self.add_exc
        return self.add_result


class FakeEvents:
    def __init__(self, events=(), fail_log_number: int | None = None):
        self.events = list(events)
        self.fail_log_number = fail_log_number
        self.log_calls = 0

    def read(self, event_type=None, since=None):
        if event_type is None:
            return list(self.events)
        return [event for event in self.events if event.event == event_type]

    def log(self, event, unit_key, payload):
        self.log_calls += 1
        if self.fail_log_number == self.log_calls:
            raise OSError("event log unavailable")
        stored = Event(
            v=1,
            ts="2026-08-18T00:00:00+00:00",
            day="2026-08-18",
            event=event,
            unit_key=unit_key,
            payload=dict(payload),
        )
        self.events.append(stored)
        return stored


class FakeConfirm:
    def __init__(self, confirmed=True, actor="human:test"):
        self.decision = ConfirmationDecision(confirmed=confirmed, actor_id=actor)
        self.previews = []

    def decide(self, preview):
        self.previews.append(preview)
        return self.decision


def metadata():
    return GenerationMetadata(
        model_id="test-model",
        model_version="1",
        prompt_version="forge-v1",
        prompt_sha256="a" * 64,
        generation_config={"temperature": 0},
    )


def request():
    return ForgeRequest(
        source_ref="dictionary:cambridge:subtle",
        source_sentence="The distinction is subtle but important.",
        learner_note="seen in reading",
    )


def run_forge(*, generator=None, anki=None, events=None, confirm=None, attempt="attempt-0001"):
    return forge(
        request(),
        deck_name="Vocabulary",
        generator=generator or FakeGenerator(),
        anki=anki or FakeAnki(),
        event_log=events or FakeEvents(),
        confirmation=confirm or FakeConfirm(),
        generation_metadata=metadata(),
        today=lambda: date(2026, 8, 18),
        attempt_id_factory=lambda: attempt,
    )


def test_happy_path_intent_precedes_acceptance():
    events = FakeEvents()
    anki = FakeAnki()
    result = run_forge(events=events, anki=anki)
    assert result.status is ForgeStatus.CREATED
    assert result.note_id == 101
    forge_events = [e for e in events.events if e.event == "FORGE"]
    assert [e.payload.get("outcome") for e in forge_events] == ["COMMIT_INTENT", None]
    assert forge_events[0].payload["accepted"] is False
    assert forge_events[1].payload["accepted"] is True
    assert forge_events[0].payload["forge_attempt_id"] == forge_events[1].payload["forge_attempt_id"]


def test_invalid_request_costs_zero_generator_calls():
    generator = FakeGenerator()
    result = forge(
        ForgeRequest("bad", "sentence"),
        deck_name="Vocabulary",
        generator=generator,
        anki=FakeAnki(),
        event_log=FakeEvents(),
        confirmation=FakeConfirm(),
        generation_metadata=metadata(),
        today=lambda: date(2026, 8, 18),
        attempt_id_factory=lambda: "attempt-0001",
    )
    assert result.status is ForgeStatus.ABORTED
    assert result.outcome == "REQUEST_INVALID"
    assert generator.calls == 0


def test_invalid_identity_returns_frozen_violation_without_event():
    output = dict(VALID_OUTPUT, lemma_slug="Bad Slug")
    events = FakeEvents()
    result = run_forge(generator=FakeGenerator(output), events=events)
    assert result.status is ForgeStatus.ABORTED
    assert result.outcome == "IDENTITY_INVALID"
    assert "F_LEMMA_SLUG_INVALID" in result.violations
    assert events.events == []


def test_validator_rejection_is_logged_with_attempt_id():
    output = dict(VALID_OUTPUT, definition_en="")
    events = FakeEvents()
    result = run_forge(generator=FakeGenerator(output), events=events)
    assert result.status is ForgeStatus.REJECTED
    assert result.outcome == "VALIDATOR_REJECTED"
    assert result.violations == ("F_DEFINITION_EMPTY",)
    assert events.events[-1].payload["forge_attempt_id"] == "attempt-0001"


def test_duplicate_does_not_write_note():
    anki = FakeAnki(found=[55])
    result = run_forge(anki=anki)
    assert result.status is ForgeStatus.REJECTED
    assert result.outcome == "DUPLICATE"
    assert anki.add_calls == 0


def test_decline_never_writes_note():
    anki = FakeAnki()
    result = run_forge(anki=anki, confirm=FakeConfirm(False, "human:test"))
    assert result.status is ForgeStatus.REJECTED
    assert result.outcome == "HUMAN_DECLINED"
    assert anki.add_calls == 0


def test_unwritable_intent_blocks_anki():
    events = FakeEvents(fail_log_number=1)
    anki = FakeAnki()
    result = run_forge(events=events, anki=anki)
    assert result.status is ForgeStatus.ABORTED
    assert result.outcome == "EVENTLOG_UNAVAILABLE"
    assert anki.add_calls == 0


def test_anki_exception_is_commit_uncertain_and_not_retried():
    events = FakeEvents()
    anki = FakeAnki(add_exc=TimeoutError("uncertain"))
    result = run_forge(events=events, anki=anki)
    assert result.status is ForgeStatus.COMMIT_UNCERTAIN
    assert result.outcome == "ANKI_COMMIT_UNCERTAIN"
    assert anki.add_calls == 1
    assert not any(e.payload.get("accepted") is True for e in events.events)


def test_acceptance_failure_keeps_confirmed_note_as_evidence_gap():
    events = FakeEvents(fail_log_number=2)
    anki = FakeAnki(add_result=[777])
    result = run_forge(events=events, anki=anki)
    assert result.status is ForgeStatus.EVIDENCE_GAP
    assert result.note_id == 777
    assert anki.add_calls == 1


def test_pending_intent_blocks_new_add_and_surfaces_gap():
    events = FakeEvents()
    first = run_forge(events=events, anki=FakeAnki(add_result=[88]))
    assert first.status is ForgeStatus.CREATED
    events.events.pop()  # simulate lost acceptance after durable intent
    anki = FakeAnki(found=[88])
    second = run_forge(events=events, anki=anki, attempt="attempt-0002")
    assert second.status is ForgeStatus.EVIDENCE_GAP
    assert second.forge_attempt_id == "attempt-0001"
    assert anki.add_calls == 0


def test_repair_uses_pending_intent_and_current_note():
    events = FakeEvents()
    run_forge(events=events, anki=FakeAnki(add_result=[88]))
    events.events.pop()  # remove acceptance, keep durable intent
    repaired = repair_evidence(
        forge_attempt_id="attempt-0001",
        anki=FakeAnki(found=[88]),
        event_log=events,
    )
    assert repaired.status is RepairStatus.REPAIRED
    assert events.events[-1].payload["accepted"] is True
    assert events.events[-1].payload["repaired"] is True


def test_generation_request_hash_covers_every_request_field():
    base = request()
    base_hash = generation_request_sha256(base)
    assert generation_request_sha256(ForgeRequest("dictionary:cambridge:other", base.source_sentence, base.learner_note)) != base_hash
    assert generation_request_sha256(ForgeRequest(base.source_ref, "A different source sentence.", base.learner_note)) != base_hash
    assert generation_request_sha256(ForgeRequest(base.source_ref, base.source_sentence, "different note")) != base_hash
