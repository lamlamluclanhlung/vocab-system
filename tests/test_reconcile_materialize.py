"""Failure-injection tests for crash-safe T9.3 materialization and recovery."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import pytest

import vocab.reconcile as reconcile_module
from vocab.card_contract import GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME
from vocab.contracts import (
    ANKI_NOTE_TYPE_NAME,
    ANKI_QUEUE_SUSPENDED,
    ANKI_SORT_FIELD,
    CARD_TEMPLATE_NAMES,
    CHANNELS,
    EVENT_SCHEMA_VERSION,
    INITIAL_NEW_EPISODE_PREFIX,
    NOTE_FIELDS,
    STATE_FIELD_BY_CHANNEL,
    TARGET_FIELD_BY_CHANNEL,
)
from vocab.events import EventLog
from vocab.models import Event, PlannedTransition, VocabUnit
from vocab.reconcile import (
    ReconcileEventHistoryError,
    ReconcileMaterializationError,
    ReconcileReactivationError,
    ReconcileRecoveryConflictError,
    ReconcileRecoveryError,
    decide_transitions,
    observe_unit,
    reactivate_relapse_channel,
    reconcile_unit,
)


NOTE_ID = 7001
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
UNIT_KEY = "subtle::small-difference"
CARD_IDS = {"R": 101, "L": 102, "W": 103, "S": 104}


def canonical_digest(identity: dict[str, object]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def initial_episode_id(channel: str) -> str:
    return INITIAL_NEW_EPISODE_PREFIX + canonical_digest(
        {"channel": channel, "unit_key": UNIT_KEY}
    )


def transition_id(
    *,
    channel: str,
    from_state: str,
    to_state: str,
    trigger: str,
    from_episode_id: str,
    evidence: dict[str, object],
) -> str:
    return canonical_digest(
        {
            "v": EVENT_SCHEMA_VERSION,
            "unit_key": UNIT_KEY,
            "channel": channel,
            "from": from_state,
            "to": to_state,
            "trigger": trigger,
            "from_episode_id": from_episode_id,
            "evidence": evidence,
        }
    )


def make_unit(states: dict[str, str]) -> VocabUnit:
    unit = VocabUnit(
        unit_key=UNIT_KEY,
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        register="neutral",
        definition_en="Difficult to notice or understand immediately.",
        source_ref="dictionary:test:subtle",
        source_sentence="The subtle distinction matters in this example.",
    )
    for channel, state in states.items():
        setattr(unit, f"Target_{channel}", "1")
        setattr(unit, f"state_{channel}", state)
    return unit


def valid_model() -> dict[str, Any]:
    field_ordinals = {name: index for index, name in enumerate(NOTE_FIELDS)}
    templates = []
    requirements = []
    for ordinal, name in enumerate(CARD_TEMPLATE_NAMES):
        target = TARGET_FIELD_BY_CHANNEL[name]
        content = {"R": "Ctx_1", "L": "audio_1"}.get(name, "lemma")
        templates.append(
            {
                "name": name,
                "ord": ordinal,
                "qfmt": f"{{{{#{target}}}}}{{{{{content}}}}}{{{{/{target}}}}}",
                "afmt": "{{FrontSide}}{{definition_en}}",
            }
        )
        generation_fields = GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME[name]
        requirements.append(
            [
                ordinal,
                "all" if len(generation_fields) > 1 else "any",
                [field_ordinals[field] for field in generation_fields],
            ]
        )
    return {
        "id": 1704387367119,
        "name": ANKI_NOTE_TYPE_NAME,
        "sortf": field_ordinals[ANKI_SORT_FIELD],
        "flds": [
            {"name": name, "ord": ordinal}
            for ordinal, name in enumerate(NOTE_FIELDS)
        ],
        "tmpls": templates,
        "req": requirements,
        "css": ".card { color: black; }",
    }


def epoch_ms(instant: datetime) -> int:
    return int(instant.timestamp() * 1000)


def review(instant: datetime) -> dict[str, int]:
    return {"id": epoch_ms(instant), "ease": 3, "type": 0, "ivl": 1}


class FakeAnki:
    def __init__(
        self,
        unit: VocabUnit,
        *,
        reviews: dict[str, list[dict[str, int]]] | None = None,
        queues: dict[str, int] | None = None,
        intervals: dict[str, int] | None = None,
        operations: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.fields = unit.to_note_fields()
        self.channels = tuple(unit.active_channel_states())
        self.card_ids = tuple(CARD_IDS[channel] for channel in self.channels)
        self.queues = {
            CARD_IDS[channel]: (0 if queues is None else queues.get(channel, 0))
            for channel in self.channels
        }
        self.intervals = {
            CARD_IDS[channel]: (
                0 if intervals is None else intervals.get(channel, 0)
            )
            for channel in self.channels
        }
        self.reviews = {
            str(CARD_IDS[channel]): (
                [] if reviews is None else deepcopy(reviews.get(channel, []))
            )
            for channel in self.channels
        }
        self.operations = [] if operations is None else operations
        self.update_attempts = 0
        self.fail_update_attempts: set[int] = set()
        self.fail_state_readback_after_update = False
        self._bad_state_readback_used = False
        self.suspend_should_fail = False
        self.unsuspend_should_fail = False

    def _note(self) -> dict[str, object]:
        return {
            "noteId": NOTE_ID,
            "modelName": ANKI_NOTE_TYPE_NAME,
            "fields": {
                name: {"value": self.fields[name], "order": index}
                for index, name in enumerate(NOTE_FIELDS)
            },
            "cards": list(self.card_ids),
            "tags": [],
        }

    def notes_info(self, note_ids: list[int]) -> list[dict[str, object]]:
        self.operations.append(("notes_info", tuple(note_ids)))
        if (
            self.fail_state_readback_after_update
            and self.update_attempts > 0
            and not self._bad_state_readback_used
        ):
            self._bad_state_readback_used = True
            return []
        return [deepcopy(self._note())]

    def verified_note_type_snapshot(self) -> dict[str, Any]:
        self.operations.append(("verified_note_type_snapshot",))
        return deepcopy(valid_model())

    def cards_info(self, card_ids: list[int]) -> list[dict[str, object]]:
        self.operations.append(("cards_info", tuple(card_ids)))
        rows = []
        for card_id in card_ids:
            channel = next(
                channel for channel, value in CARD_IDS.items() if value == card_id
            )
            rows.append(
                {
                    "cardId": card_id,
                    "note": NOTE_ID,
                    "ord": CHANNELS.index(channel),
                    "interval": self.intervals[card_id],
                    "lapses": 0,
                    "queue": self.queues[card_id],
                    "reps": 0,
                }
            )
        return rows

    def get_revlog(
        self,
        card_ids: list[int],
    ) -> dict[str, list[dict[str, int]]]:
        self.operations.append(("get_revlog", tuple(card_ids)))
        return {str(card_id): deepcopy(self.reviews[str(card_id)]) for card_id in card_ids}

    def update_note_fields(
        self,
        note_id: int,
        fields: dict[str, str],
    ) -> None:
        self.update_attempts += 1
        self.operations.append(("update_note_fields", note_id, dict(fields)))
        if self.update_attempts in self.fail_update_attempts:
            raise RuntimeError("injected update failure")
        self.fields.update(fields)

    def suspend(self, card_ids: list[int]) -> bool:
        self.operations.append(("suspend", tuple(card_ids)))
        if self.suspend_should_fail:
            raise RuntimeError("injected suspend failure")
        for card_id in card_ids:
            self.queues[card_id] = ANKI_QUEUE_SUSPENDED
        return True

    def unsuspend(self, card_ids: list[int]) -> None:
        self.operations.append(("unsuspend", tuple(card_ids)))
        if self.unsuspend_should_fail:
            raise RuntimeError("injected unsuspend failure")
        for card_id in card_ids:
            self.queues[card_id] = 0


class FakeEventLog:
    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        operations: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.events = [] if events is None else list(events)
        self.operations = [] if operations is None else operations
        self.log_attempts = 0
        self.fail_log_attempts: set[int] = set()

    def read(self) -> list[Event]:
        self.operations.append(("event_read",))
        return list(self.events)

    def log(
        self,
        event: str,
        unit_key: str,
        payload: dict[str, Any],
    ) -> Event:
        self.log_attempts += 1
        phase = payload.get("phase")
        self.operations.append(("event_log", phase, payload.get("channel")))
        if self.log_attempts in self.fail_log_attempts:
            raise OSError("injected journal append failure")
        instant = NOW - timedelta(seconds=1) + timedelta(
            milliseconds=self.log_attempts
        )
        stored = Event(
            v=EVENT_SCHEMA_VERSION,
            ts=instant.isoformat(),
            day=instant.date().isoformat(),
            event=event,
            unit_key=unit_key,
            payload=deepcopy(payload),
        )
        self.events.append(stored)
        return stored


def event_for_payload(
    payload: dict[str, object],
    *,
    instant: datetime,
) -> Event:
    return Event(
        v=EVENT_SCHEMA_VERSION,
        ts=instant.isoformat(),
        day=instant.date().isoformat(),
        event="STATE",
        unit_key=UNIT_KEY,
        payload=deepcopy(payload),
    )


def payload_for_plan(plan: PlannedTransition, phase: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "channel": plan.channel,
        "from": plan.from_state,
        "to": plan.to_state,
        "trigger": plan.trigger,
        "transition_id": plan.transition_id,
        "from_episode_id": plan.from_episode_id,
        "phase": phase,
        "evidence": deepcopy(plan.evidence),
    }
    if plan.transition_group_id:
        payload["transition_group_id"] = plan.transition_group_id
    return payload


def committed_chain(
    channel: str,
    steps: tuple[tuple[str, str, str], ...],
    *,
    final_commit_at: datetime,
) -> tuple[list[Event], str]:
    events: list[Event] = []
    episode_id = initial_episode_id(channel)
    first_commit = final_commit_at - timedelta(seconds=len(steps) - 1)
    for index, (from_state, to_state, trigger) in enumerate(steps):
        evidence = {"fixture": f"{channel}:{from_state}:{to_state}"}
        current_id = transition_id(
            channel=channel,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            from_episode_id=episode_id,
            evidence=evidence,
        )
        commit_at = first_commit + timedelta(seconds=index)
        base = {
            "channel": channel,
            "from": from_state,
            "to": to_state,
            "trigger": trigger,
            "transition_id": current_id,
            "from_episode_id": episode_id,
            "evidence": evidence,
        }
        for phase, instant in (
            ("PREPARE", commit_at - timedelta(milliseconds=1)),
            ("COMMIT", commit_at),
        ):
            payload = dict(base)
            payload["phase"] = phase
            events.append(event_for_payload(payload, instant=instant))
        episode_id = current_id
    return events, episode_id


def new_fixture(
    channels: tuple[str, ...] = ("R",),
    *,
    reviewed: bool = True,
) -> tuple[FakeAnki, FakeEventLog, list[tuple[object, ...]]]:
    operations: list[tuple[object, ...]] = []
    unit = make_unit({channel: "NEW" for channel in channels})
    reviews = {
        channel: (
            [review(NOW - 2 * timedelta(days=1) + timedelta(milliseconds=index))]
            if reviewed
            else []
        )
        for index, channel in enumerate(channels)
    }
    anki = FakeAnki(unit, reviews=reviews, operations=operations)
    event_log = FakeEventLog(operations=operations)
    return anki, event_log, operations


def dormancy_fixture(
    *,
    states: dict[str, str] | None = None,
    queues: dict[str, int] | None = None,
) -> tuple[
    FakeAnki,
    FakeEventLog,
    tuple[PlannedTransition, ...],
    list[Event],
    list[tuple[object, ...]],
]:
    selected_states = (
        {"R": "MASTERED", "L": "MASTERED"}
        if states is None
        else states
    )
    operations: list[tuple[object, ...]] = []
    events: list[Event] = []
    mastered_episodes: dict[str, str] = {}
    final_times = {"R": NOW - 35 * timedelta(days=1), "L": NOW - 30 * timedelta(days=1)}
    for channel in ("R", "L"):
        channel_events, episode_id = committed_chain(
            channel,
            (
                ("NEW", "LEARNING", "FIRST_REVIEW"),
                ("LEARNING", "STABLE", "STABILITY_GATE"),
                ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
            ),
            final_commit_at=final_times[channel],
        )
        events.extend(channel_events)
        mastered_episodes[channel] = episode_id
    unit = make_unit(selected_states)
    anki = FakeAnki(unit, queues=queues, operations=operations)
    event_log = FakeEventLog(events, operations=operations)
    if all(state == "MASTERED" for state in selected_states.values()):
        progress = observe_unit(
            NOTE_ID,
            anki=anki,
            event_log=event_log,
            now=NOW,
        )
        plans = decide_transitions(progress, now=NOW).transitions
    else:
        plans = ()
    operations.clear()
    return anki, event_log, plans, events, operations


def add_plan_events(
    event_log: FakeEventLog,
    plans: tuple[PlannedTransition, ...],
    phases_by_channel: dict[str, tuple[str, ...]],
) -> None:
    offset = 0
    for plan in plans:
        for phase in phases_by_channel.get(plan.channel, ()):
            offset += 1
            event_log.events.append(
                event_for_payload(
                    payload_for_plan(plan, phase),
                    instant=NOW - timedelta(minutes=1) + timedelta(milliseconds=offset),
                )
            )


def successful_phases(event_log: FakeEventLog) -> tuple[str, ...]:
    return tuple(
        str(event.payload["phase"])
        for event in event_log.events
        if event.event == "STATE" and "phase" in event.payload
    )


def mutation_operations(operations: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    return [
        item
        for item in operations
        if item[0] in {"update_note_fields", "suspend", "unsuspend"}
    ]


def test_noop_has_zero_state_or_anki_writes() -> None:
    anki, event_log, operations = new_fixture(reviewed=False)
    result = reconcile_unit(
        NOTE_ID,
        anki=anki,
        event_log=event_log,
        now=NOW,
    )
    assert result.committed_transition_ids == ()
    assert event_log.events == []
    assert mutation_operations(operations) == []


def test_normal_transition_order_and_payload_identity_are_exact() -> None:
    anki, event_log, operations = new_fixture()
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    start = next(index for index, item in enumerate(operations) if item[0] == "event_log")
    assert operations[start:] == [
        ("event_log", "PREPARE", "R"),
        ("update_note_fields", NOTE_ID, {"state_R": "LEARNING"}),
        ("notes_info", (NOTE_ID,)),
        ("event_log", "COMMIT", "R"),
    ]
    prepare, commit = event_log.events
    assert prepare.payload["phase"] == "PREPARE"
    assert commit.payload["phase"] == "COMMIT"
    prepare_identity = dict(prepare.payload)
    commit_identity = dict(commit.payload)
    del prepare_identity["phase"]
    del commit_identity["phase"]
    assert prepare_identity == commit_identity
    assert result.committed_transition_ids == (prepare.payload["transition_id"],)


def test_prepare_failure_causes_zero_anki_mutation() -> None:
    anki, event_log, operations = new_fixture()
    event_log.fail_log_attempts = {1}
    with pytest.raises(ReconcileMaterializationError, match="PREPARE"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert event_log.events == []
    assert mutation_operations(operations) == []


def test_update_failure_leaves_prepare_without_commit_or_abort() -> None:
    anki, event_log, operations = new_fixture()
    anki.fail_update_attempts = {1}
    with pytest.raises(ReconcileMaterializationError, match="state update"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert successful_phases(event_log) == ("PREPARE",)
    assert all(event.payload["phase"] != "ABORT" for event in event_log.events)
    assert [item[0] for item in mutation_operations(operations)] == [
        "update_note_fields"
    ]


def test_state_readback_failure_leaves_prepare_without_commit() -> None:
    anki, event_log, _operations = new_fixture()
    anki.fail_state_readback_after_update = True
    with pytest.raises(ReconcileMaterializationError, match="readback"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert successful_phases(event_log) == ("PREPARE",)
    assert anki.fields["state_R"] == "LEARNING"


def test_commit_failure_recovers_target_without_duplicate_update() -> None:
    anki, event_log, operations = new_fixture()
    event_log.fail_log_attempts = {2}
    with pytest.raises(ReconcileMaterializationError, match="COMMIT"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert successful_phases(event_log) == ("PREPARE",)
    assert anki.fields["state_R"] == "LEARNING"
    event_log.fail_log_attempts.clear()

    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert result.committed_transition_ids == result.recovered_transition_ids
    assert successful_phases(event_log) == ("PREPARE", "COMMIT")
    assert len([item for item in operations if item[0] == "update_note_fields"]) == 1


def test_multiple_independent_channels_commit_in_channel_order() -> None:
    anki, event_log, operations = new_fixture(("L", "R"))
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert tuple(event.payload["channel"] for event in event_log.events) == (
        "R",
        "R",
        "L",
        "L",
    )
    assert tuple(
        item[2] for item in operations if item[0] == "update_note_fields"
    ) == ({"state_R": "LEARNING"}, {"state_L": "LEARNING"})
    assert len(result.committed_transition_ids) == 2


def test_first_commit_survives_failure_of_second_channel() -> None:
    anki, event_log, operations = new_fixture(("R", "L"))
    anki.fail_update_attempts = {2}
    with pytest.raises(ReconcileMaterializationError):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert successful_phases(event_log) == ("PREPARE", "COMMIT", "PREPARE")
    assert anki.fields["state_R"] == "LEARNING"
    assert anki.fields["state_L"] == "NEW"
    assert len([item for item in operations if item[0] == "update_note_fields"]) == 2


def test_mutated_decision_evidence_is_rejected_before_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anki, event_log, operations = new_fixture()
    real_decide = reconcile_module.decide_transitions

    def mutated_decide(progress: object, *, now: datetime) -> object:
        decision = real_decide(progress, now=now)
        decision.transitions[0].evidence["mutated"] = True
        return decision

    monkeypatch.setattr(reconcile_module, "decide_transitions", mutated_decide)
    with pytest.raises(ReconcileMaterializationError, match="transition_id"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert event_log.events == []
    assert mutation_operations(operations) == []


def pending_new_transition() -> tuple[FakeAnki, FakeEventLog, PlannedTransition, list[tuple[object, ...]]]:
    anki, event_log, operations = new_fixture()
    progress = observe_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    plan = decide_transitions(progress, now=NOW).transitions[0]
    event_log.events.append(
        event_for_payload(
            payload_for_plan(plan, "PREPARE"),
            instant=NOW - timedelta(minutes=1),
        )
    )
    operations.clear()
    return anki, event_log, plan, operations


def test_source_state_recovery_resumes_without_duplicate_prepare() -> None:
    anki, event_log, plan, _operations = pending_new_transition()
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert successful_phases(event_log) == ("PREPARE", "COMMIT")
    assert result.committed_transition_ids == (plan.transition_id,)
    assert result.recovered_transition_ids == (plan.transition_id,)


def test_source_state_with_changed_fresh_plan_aborts_without_anki_mutation() -> None:
    anki, event_log, plan, operations = pending_new_transition()
    anki.reviews[str(CARD_IDS["R"])] = [review(NOW - timedelta(days=1))]
    with pytest.raises(ReconcileRecoveryConflictError) as exc_info:
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert exc_info.value.aborted_transition_ids == (plan.transition_id,)
    assert successful_phases(event_log) == ("PREPARE", "ABORT")
    assert mutation_operations(operations) == []


def test_state_neither_source_nor_target_aborts_and_fails_closed() -> None:
    anki, event_log, plan, operations = pending_new_transition()
    anki.fields["state_R"] = "STABLE"
    with pytest.raises(ReconcileRecoveryConflictError) as exc_info:
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert exc_info.value.aborted_transition_ids == (plan.transition_id,)
    assert successful_phases(event_log) == ("PREPARE", "ABORT")
    assert mutation_operations(operations) == []


def test_recovery_first_handles_target_that_direct_observation_rejects() -> None:
    anki, event_log, plan, operations = pending_new_transition()
    anki.fields["state_R"] = "LEARNING"
    anki.intervals[CARD_IDS["R"]] = 100
    with pytest.raises(ReconcileEventHistoryError, match="reconstructed"):
        observe_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    operations.clear()

    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert result.committed_transition_ids == (plan.transition_id,)
    assert result.recovered_transition_ids == (plan.transition_id,)
    assert successful_phases(event_log) == ("PREPARE", "COMMIT")
    assert not any(item[0] == "update_note_fields" for item in operations)
    assert anki.fields["state_R"] == "LEARNING"


def test_normal_dormancy_group_orders_all_boundaries_and_updates_only_states() -> None:
    anki, event_log, plans, _events, operations = dormancy_fixture()
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    added = event_log.events[-4:]
    assert tuple((item.payload["phase"], item.payload["channel"]) for item in added) == (
        ("PREPARE", "R"),
        ("PREPARE", "L"),
        ("COMMIT", "R"),
        ("COMMIT", "L"),
    )
    start = next(index for index, item in enumerate(operations) if item[0] == "event_log")
    assert operations[start:] == [
        ("event_log", "PREPARE", "R"),
        ("event_log", "PREPARE", "L"),
        (
            "update_note_fields",
            NOTE_ID,
            {"state_R": "DORMANT", "state_L": "DORMANT"},
        ),
        ("notes_info", (NOTE_ID,)),
        ("suspend", (101, 102)),
        ("cards_info", (101, 102)),
        ("event_log", "COMMIT", "R"),
        ("event_log", "COMMIT", "L"),
    ]
    updates = [item[2] for item in operations if item[0] == "update_note_fields"]
    assert updates == [{"state_R": "DORMANT", "state_L": "DORMANT"}]
    assert all(set(update).issubset(STATE_FIELD_BY_CHANNEL.values()) for update in updates)
    assert result.committed_transition_ids == tuple(plan.transition_id for plan in plans)
    assert not any(item[0] == "unsuspend" for item in operations)


def test_partial_dormancy_prepare_appends_only_missing_member_before_mutation() -> None:
    anki, event_log, plans, _events, operations = dormancy_fixture()
    add_plan_events(event_log, plans, {"R": ("PREPARE",)})
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    first_mutation = next(index for index, item in enumerate(operations) if item[0] == "update_note_fields")
    assert ("event_log", "PREPARE", "L") in operations[:first_mutation]
    assert operations.count(("event_log", "PREPARE", "R")) == 0
    assert len([item for item in operations if item[0] == "update_note_fields"]) == 1
    assert result.recovered_transition_ids == (plans[0].transition_id,)
    assert result.committed_transition_ids == tuple(plan.transition_id for plan in plans)


def test_all_dormancy_prepares_resume_with_one_subset_state_write() -> None:
    anki, event_log, plans, _events, operations = dormancy_fixture()
    add_plan_events(
        event_log,
        plans,
        {"R": ("PREPARE",), "L": ("PREPARE",)},
    )
    reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    updates = [item for item in operations if item[0] == "update_note_fields"]
    assert len(updates) == 1
    assert updates[0][2] == {"state_R": "DORMANT", "state_L": "DORMANT"}
    assert not any(
        item[0] == "event_log" and item[1] == "PREPARE" for item in operations
    )


def target_dormancy_recovery(
    *,
    queues: dict[str, int],
    committed_r: bool = False,
) -> tuple[FakeAnki, FakeEventLog, tuple[PlannedTransition, ...], list[tuple[object, ...]]]:
    source_anki, source_log, plans, chain_events, _operations = dormancy_fixture()
    del source_anki
    operations: list[tuple[object, ...]] = []
    target = FakeAnki(
        make_unit({"R": "DORMANT", "L": "DORMANT"}),
        queues=queues,
        operations=operations,
    )
    log = FakeEventLog(list(chain_events), operations=operations)
    phases = {
        "R": ("PREPARE", "COMMIT") if committed_r else ("PREPARE",),
        "L": ("PREPARE",),
    }
    add_plan_events(log, plans, phases)
    return target, log, plans, operations


def test_recovery_after_dormancy_state_write_completes_suspension_then_commit() -> None:
    anki, event_log, plans, operations = target_dormancy_recovery(
        queues={"R": 0, "L": 0}
    )
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert ("suspend", (101, 102)) in operations
    suspend_index = operations.index(("suspend", (101, 102)))
    first_commit = next(
        index
        for index, item in enumerate(operations)
        if item[0] == "event_log" and item[1] == "COMMIT"
    )
    assert suspend_index < first_commit
    assert result.recovered_transition_ids == tuple(plan.transition_id for plan in plans)


def test_recovery_after_dormancy_suspension_does_not_suspend_twice() -> None:
    anki, event_log, plans, operations = target_dormancy_recovery(
        queues={"R": ANKI_QUEUE_SUSPENDED, "L": ANKI_QUEUE_SUSPENDED}
    )
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert not any(item[0] == "suspend" for item in operations)
    assert result.committed_transition_ids == tuple(plan.transition_id for plan in plans)


def test_mixed_dormancy_commit_appends_only_missing_commit() -> None:
    anki, event_log, plans, operations = target_dormancy_recovery(
        queues={"R": ANKI_QUEUE_SUSPENDED, "L": ANKI_QUEUE_SUSPENDED},
        committed_r=True,
    )
    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    commit_calls = [
        item for item in operations if item[0] == "event_log" and item[1] == "COMMIT"
    ]
    assert commit_calls == [("event_log", "COMMIT", "L")]
    assert result.committed_transition_ids == (plans[1].transition_id,)
    assert result.recovered_transition_ids == (plans[1].transition_id,)


def test_mixed_dormancy_states_abort_pending_members_and_fail_closed() -> None:
    _source_anki, _source_log, plans, chain_events, _source_operations = (
        dormancy_fixture()
    )
    operations: list[tuple[object, ...]] = []
    anki = FakeAnki(
        make_unit({"R": "DORMANT", "L": "MASTERED"}),
        operations=operations,
    )
    event_log = FakeEventLog(list(chain_events), operations=operations)
    add_plan_events(
        event_log,
        plans,
        {"R": ("PREPARE",), "L": ("PREPARE",)},
    )
    with pytest.raises(ReconcileRecoveryConflictError) as exc_info:
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert exc_info.value.aborted_transition_ids == tuple(
        plan.transition_id for plan in plans
    )
    assert not any(item[0] == "update_note_fields" for item in operations)


def test_wrong_dormancy_group_identity_fails_before_anki_mutation() -> None:
    anki, event_log, plans, _events, operations = dormancy_fixture()
    payload = payload_for_plan(plans[0], "PREPARE")
    payload["transition_group_id"] = "0" * 64
    event_log.events.append(
        event_for_payload(payload, instant=NOW - timedelta(minutes=1))
    )
    with pytest.raises(ReconcileMaterializationError, match="group_id|group"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert mutation_operations(operations) == []


def test_dormancy_member_identity_mismatch_fails_before_anki_mutation() -> None:
    anki, event_log, plans, _events, operations = dormancy_fixture()
    payload = payload_for_plan(plans[0], "PREPARE")
    payload["from_episode_id"] = "x" * 64
    payload["transition_id"] = transition_id(
        channel="R",
        from_state="MASTERED",
        to_state="DORMANT",
        trigger="DORMANCY_ELAPSED",
        from_episode_id="x" * 64,
        evidence=deepcopy(plans[0].evidence),
    )
    event_log.events.append(
        event_for_payload(payload, instant=NOW - timedelta(minutes=1))
    )
    with pytest.raises(ReconcileRecoveryError, match="identity|group"):
        reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    assert mutation_operations(operations) == []


def relapse_fixture(
    *,
    queue_r: int,
    include_l: bool = False,
) -> tuple[FakeAnki, FakeEventLog, list[tuple[object, ...]]]:
    states = {"R": "RELAPSE"}
    if include_l:
        states["L"] = "NEW"
    events, _episode = committed_chain(
        "R",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
            ("MASTERED", "RELAPSE", "ASSESSMENT_FAIL"),
        ),
        final_commit_at=NOW - timedelta(days=10),
    )
    operations: list[tuple[object, ...]] = []
    queues = {"R": queue_r}
    if include_l:
        queues["L"] = ANKI_QUEUE_SUSPENDED
    anki = FakeAnki(make_unit(states), queues=queues, operations=operations)
    event_log = FakeEventLog(events, operations=operations)
    return anki, event_log, operations


def test_automatic_dormant_relapse_reports_reactivation_without_unsuspending() -> None:
    events, _episode = committed_chain(
        "R",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
            ("MASTERED", "DORMANT", "DORMANCY_ELAPSED"),
        ),
        final_commit_at=NOW - timedelta(days=10),
    )
    judge_at = NOW - timedelta(days=1)
    events.append(
        Event(
            v=EVENT_SCHEMA_VERSION,
            ts=judge_at.isoformat(),
            day=judge_at.date().isoformat(),
            event="JUDGE",
            unit_key=UNIT_KEY,
            payload={
                "channel": "R",
                "passed": False,
                "assessment_id": "assessment-relapse",
                "stimulus_ref": "stimulus-relapse",
                "novel": True,
                "model_id": "human",
                "model_version": "1",
            },
        )
    )
    operations: list[tuple[object, ...]] = []
    anki = FakeAnki(
        make_unit({"R": "DORMANT"}),
        queues={"R": ANKI_QUEUE_SUSPENDED},
        operations=operations,
    )
    event_log = FakeEventLog(events, operations=operations)

    result = reconcile_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)

    assert result.reactivation_required_card_ids == (101,)
    assert anki.fields["state_R"] == "RELAPSE"
    assert anki.queues[101] == ANKI_QUEUE_SUSPENDED
    assert not any(item[0] == "unsuspend" for item in operations)


def test_reactivation_requires_literal_confirmation_before_any_mutation() -> None:
    anki, event_log, operations = relapse_fixture(queue_r=ANKI_QUEUE_SUSPENDED)
    with pytest.raises(ReconcileReactivationError, match="confirmed"):
        reactivate_relapse_channel(
            NOTE_ID,
            "R",
            anki=anki,
            event_log=event_log,
            now=NOW,
            confirmed=False,
        )
    assert operations == []


def test_wrong_reactivation_state_causes_zero_mutation() -> None:
    anki, event_log, operations = new_fixture(reviewed=False)
    with pytest.raises(ReconcileReactivationError, match="RELAPSE"):
        reactivate_relapse_channel(
            NOTE_ID,
            "R",
            anki=anki,
            event_log=event_log,
            now=NOW,
            confirmed=True,
        )
    assert mutation_operations(operations) == []


def test_confirmed_relapse_reactivation_unsuspends_exact_card_and_reads_back() -> None:
    anki, event_log, operations = relapse_fixture(
        queue_r=ANKI_QUEUE_SUSPENDED,
        include_l=True,
    )
    event_count = len(event_log.events)
    changed = reactivate_relapse_channel(
        NOTE_ID,
        "R",
        anki=anki,
        event_log=event_log,
        now=NOW,
        confirmed=True,
    )
    assert changed is True
    assert ("unsuspend", (101,)) in operations
    unsuspend_index = operations.index(("unsuspend", (101,)))
    assert operations[unsuspend_index + 1] == ("cards_info", (101,))
    assert anki.queues[101] != ANKI_QUEUE_SUSPENDED
    assert anki.queues[102] == ANKI_QUEUE_SUSPENDED
    assert len(event_log.events) == event_count


def test_already_active_relapse_returns_false_without_mutation() -> None:
    anki, event_log, operations = relapse_fixture(queue_r=0)
    assert reactivate_relapse_channel(
        NOTE_ID,
        "R",
        anki=anki,
        event_log=event_log,
        now=NOW,
        confirmed=True,
    ) is False
    assert not any(item[0] == "unsuspend" for item in operations)


def test_real_eventlog_materialization_is_readable_and_verifies_chain(
    tmp_path: object,
) -> None:
    path = tmp_path / "events.jsonl"
    event_log = EventLog(path)
    current = datetime.now(timezone.utc)
    unit = make_unit({"R": "NEW"})
    anki = FakeAnki(
        unit,
        reviews={"R": [review(current - timedelta(days=1))]},
    )

    result = reconcile_unit(
        NOTE_ID,
        anki=anki,
        event_log=event_log,
        now=current,
    )
    stored = event_log.read()
    assert tuple(event.payload["phase"] for event in stored) == (
        "PREPARE",
        "COMMIT",
    )
    assert result.committed_transition_ids == (
        stored[0].payload["transition_id"],
    )
    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=event_log,
        now=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    assert progress.channels[0].state == "LEARNING"
    assert progress.channels[0].state_episode_id == result.committed_transition_ids[0]
