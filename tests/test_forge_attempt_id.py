"""Focused regression tests for globally unique Forge attempt IDs."""

from __future__ import annotations

from vocab.forge.pipeline import _AttemptAllocator
from vocab.models import Event


class FakeEventLog:
    def __init__(self, events=(), *, read_error: Exception | None = None) -> None:
        self.events = list(events)
        self.read_error = read_error
        self.read_calls = 0

    def read(self, event_type=None, since=None):
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return [
            event
            for event in self.events
            if event_type is None or event.event == event_type
        ]

    def log(self, event, unit_key, payload):  # pragma: no cover - not used here
        raise AssertionError("allocator collision checks must not append events")


def forge_event(attempt_id: str) -> Event:
    return Event(
        v=1,
        ts="2026-08-18T00:00:00+00:00",
        day="2026-08-18",
        event="FORGE",
        unit_key="subtle::small-difference",
        payload={
            "source_ref": "dictionary:cambridge:subtle",
            "accepted": False,
            "outcome": "VALIDATOR_REJECTED",
            "forge_attempt_id": attempt_id,
        },
    )


def test_allocator_rejects_attempt_id_that_already_exists_in_history() -> None:
    event_log = FakeEventLog([forge_event("attempt-123")])
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "attempt-123"

    allocator = _AttemptAllocator(factory, event_log)

    assert allocator.get() is None
    assert allocator.failure_outcome == "ATTEMPT_ID_INVALID"
    assert calls == 1
    assert event_log.read_calls == 1


def test_allocator_accepts_new_attempt_id_and_reuses_it_without_second_read() -> None:
    event_log = FakeEventLog([forge_event("attempt-old")])
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "attempt-new"

    allocator = _AttemptAllocator(factory, event_log)

    assert allocator.get() == "attempt-new"
    assert allocator.get() == "attempt-new"
    assert calls == 1
    assert event_log.read_calls == 1


def test_allocator_fails_closed_when_history_cannot_be_read() -> None:
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "attempt-new"

    allocator = _AttemptAllocator(
        factory,
        FakeEventLog(read_error=OSError("event log unavailable")),
    )

    assert allocator.get() is None
    assert allocator.failure_outcome == "EVENTLOG_UNAVAILABLE"
    # History is checked before consuming a fresh correlation ID.
    assert calls == 0
