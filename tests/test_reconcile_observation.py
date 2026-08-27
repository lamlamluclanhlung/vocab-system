"""Deterministic tests for the read-only T9.1 observation layer."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import pytest
import vocab.reconcile as reconcile_module

from vocab.anki import (
    AnkiConnectClient,
    AnkiNoteTypeMismatchError,
    AnkiResponseError,
)
from vocab.card_contract import GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME
from vocab.contracts import (
    ANKI_NOTE_TYPE_NAME,
    ANKI_SORT_FIELD,
    CARD_TEMPLATE_NAMES,
    INITIAL_NEW_EPISODE_PREFIX,
    LIFECYCLE_SECONDS_PER_DAY,
    NOTE_FIELDS,
    STABLE_ZERO_LAPSE_WINDOW_DAYS,
    TARGET_FIELD_BY_CHANNEL,
)
from vocab.models import Event, VocabUnit
from vocab.reconcile import (
    ReconcileCardError,
    ReconcileEventHistoryError,
    ReconcileRevlogError,
    observe_unit,
)


NOTE_ID = 7001
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def make_unit(
    *,
    states: dict[str, str] | None = None,
    unit_key: str = "subtle::small-difference",
) -> VocabUnit:
    selected = {"R": "NEW"} if states is None else states
    unit = VocabUnit(
        unit_key=unit_key,
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        register="neutral",
        definition_en="Difficult to notice or understand immediately.",
        source_ref="dictionary:test:subtle",
        source_sentence="The subtle distinction matters in this example.",
    )
    for channel, state in selected.items():
        setattr(unit, f"Target_{channel}", "1")
        setattr(unit, f"state_{channel}", state)
    return unit


def valid_model(
    ordinals: dict[str, int] | None = None,
) -> dict[str, Any]:
    selected = (
        {name: index for index, name in enumerate(CARD_TEMPLATE_NAMES)}
        if ordinals is None
        else dict(ordinals)
    )
    field_ordinals = {name: index for index, name in enumerate(NOTE_FIELDS)}
    templates = []
    requirements = []
    for name in CARD_TEMPLATE_NAMES:
        ordinal = selected[name]
        target = TARGET_FIELD_BY_CHANNEL[name]
        content = {"R": "Ctx_1", "L": "audio_1"}.get(name, "lemma")
        templates.append(
            {
                "name": name,
                "ord": ordinal,
                "qfmt": (
                    f"{{{{#{target}}}}}{{{{{content}}}}}"
                    f"{{{{/{target}}}}}"
                ),
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


def note_record(
    unit: VocabUnit,
    card_ids: list[int],
    *,
    tags: list[str] | None = None,
) -> dict[str, object]:
    values = unit.to_note_fields()
    return {
        "noteId": NOTE_ID,
        "modelName": ANKI_NOTE_TYPE_NAME,
        "fields": {
            name: {"value": values[name], "order": index}
            for index, name in enumerate(NOTE_FIELDS)
        },
        "cards": list(card_ids),
        "tags": [] if tags is None else list(tags),
    }


def card_record(
    card_id: int,
    ordinal: int,
    *,
    note_id: int = NOTE_ID,
    interval: int = 0,
    lapses: int = 0,
    queue: int = 0,
) -> dict[str, object]:
    return {
        "cardId": card_id,
        "note": note_id,
        "ord": ordinal,
        "interval": interval,
        "lapses": lapses,
        "queue": queue,
        "reps": 0,
    }


def epoch_ms(instant: datetime) -> int:
    return int(instant.timestamp() * 1000)


def review(
    instant: datetime,
    *,
    review_type: int,
    ease: int,
    interval: int = 1,
) -> dict[str, int]:
    return {
        "id": epoch_ms(instant),
        "ease": ease,
        "type": review_type,
        "ivl": interval,
    }


def stored_event(
    event_type: str,
    payload: dict[str, object],
    *,
    ts: datetime,
    unit_key: str = "subtle::small-difference",
) -> Event:
    return Event(
        v=1,
        ts=ts.isoformat(),
        day=ts.date().isoformat(),
        event=event_type,
        unit_key=unit_key,
        payload=dict(payload),
    )


def canonical_digest(identity: dict[str, object]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def initial_episode_id(
    channel: str,
    unit_key: str = "subtle::small-difference",
) -> str:
    return INITIAL_NEW_EPISODE_PREFIX + canonical_digest(
        {"channel": channel, "unit_key": unit_key}
    )


def state_payload(
    *,
    channel: str = "R",
    from_state: str = "NEW",
    to_state: str = "LEARNING",
    trigger: str = "FIRST_REVIEW",
    from_episode_id: str | None = None,
    phase: str = "PREPARE",
    evidence: dict[str, object] | None = None,
    transition_id: str | None = None,
    transition_group_id: str | None = None,
    unit_key: str = "subtle::small-difference",
) -> dict[str, object]:
    selected_episode_id = (
        initial_episode_id(channel, unit_key)
        if from_episode_id is None
        else from_episode_id
    )
    selected_evidence = (
        {"fixture": f"{channel}:{from_state}:{to_state}:{trigger}"}
        if evidence is None
        else evidence
    )
    expected_transition_id = canonical_digest(
        {
            "v": 1,
            "unit_key": unit_key,
            "channel": channel,
            "from": from_state,
            "to": to_state,
            "trigger": trigger,
            "from_episode_id": selected_episode_id,
            "evidence": selected_evidence,
        }
    )
    payload: dict[str, object] = {
        "channel": channel,
        "from": from_state,
        "to": to_state,
        "trigger": trigger,
        "transition_id": (
            expected_transition_id if transition_id is None else transition_id
        ),
        "from_episode_id": selected_episode_id,
        "phase": phase,
        "evidence": selected_evidence,
    }
    if transition_group_id is not None:
        payload["transition_group_id"] = transition_group_id
    return payload


def journal_transition(
    *,
    channel: str = "R",
    from_state: str = "NEW",
    to_state: str = "LEARNING",
    trigger: str = "FIRST_REVIEW",
    from_episode_id: str | None = None,
    evidence: dict[str, object] | None = None,
    phases: tuple[str, ...] = ("PREPARE", "COMMIT"),
    terminal_ts: datetime = NOW - timedelta(days=1),
    unit_key: str = "subtle::small-difference",
) -> tuple[list[Event], str]:
    base = state_payload(
        channel=channel,
        from_state=from_state,
        to_state=to_state,
        trigger=trigger,
        from_episode_id=from_episode_id,
        evidence=evidence,
        unit_key=unit_key,
    )
    transition_id = str(base["transition_id"])
    events = []
    for phase in phases:
        payload = dict(base)
        payload["phase"] = phase
        ts = (
            terminal_ts - timedelta(milliseconds=1)
            if phase == "PREPARE" and len(phases) > 1
            else terminal_ts
        )
        events.append(
            stored_event("STATE", payload, ts=ts, unit_key=unit_key)
        )
    return events, transition_id


def committed_chain(
    channel: str,
    steps: tuple[tuple[str, str, str], ...],
    *,
    first_terminal_ts: datetime,
) -> tuple[list[Event], str]:
    events: list[Event] = []
    episode_id = initial_episode_id(channel)
    for index, (from_state, to_state, trigger) in enumerate(steps):
        transition_events, episode_id = journal_transition(
            channel=channel,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            from_episode_id=episode_id,
            terminal_ts=first_terminal_ts + timedelta(seconds=index),
        )
        events.extend(transition_events)
    return events, episode_id


class FakeAnki:
    def __init__(
        self,
        unit: VocabUnit,
        cards: list[dict[str, object]],
        *,
        model: dict[str, Any] | None = None,
        revlog: dict[str, list[dict[str, int]]] | None = None,
        tags: list[str] | None = None,
        note_cards: list[int] | None = None,
    ) -> None:
        card_ids = (
            [int(card["cardId"]) for card in cards]
            if note_cards is None
            else list(note_cards)
        )
        self.note = note_record(unit, card_ids, tags=tags)
        self.model = valid_model() if model is None else model
        self.cards = cards
        self.revlog = (
            {str(card_id): [] for card_id in card_ids}
            if revlog is None
            else revlog
        )
        self.read_calls: list[tuple[str, object]] = []
        self.mutation_calls: list[tuple[str, object]] = []

    def notes_info(self, note_ids: list[int]) -> list[dict[str, object]]:
        self.read_calls.append(("notes_info", list(note_ids)))
        return [deepcopy(self.note)]

    def verified_note_type_snapshot(self) -> dict[str, Any]:
        self.read_calls.append(("verified_note_type_snapshot", None))
        return deepcopy(self.model)

    def cards_info(self, card_ids: list[int]) -> list[dict[str, object]]:
        self.read_calls.append(("cards_info", list(card_ids)))
        return deepcopy(self.cards)

    def get_revlog(
        self,
        card_ids: list[int],
    ) -> dict[str, list[dict[str, int]]]:
        self.read_calls.append(("get_revlog", list(card_ids)))
        return deepcopy(self.revlog)

    def update_note_fields(self, *args: object, **kwargs: object) -> None:
        self.mutation_calls.append(("update_note_fields", (args, kwargs)))

    def suspend(self, card_ids: list[int]) -> bool:
        self.mutation_calls.append(("suspend", list(card_ids)))
        return True

    def unsuspend(self, card_ids: list[int]) -> None:
        self.mutation_calls.append(("unsuspend", list(card_ids)))


class FakeEventLog:
    def __init__(self, events: list[Event] | None = None) -> None:
        self.events = [] if events is None else list(events)
        self.read_calls = 0
        self.log_calls: list[tuple[object, ...]] = []

    def read(self) -> list[Event]:
        self.read_calls += 1
        return list(self.events)

    def read_strict(self) -> list[Event]:
        return self.read()

    def log(self, *args: object, **kwargs: object) -> None:
        self.log_calls.append((*args, kwargs))


def default_anki(
    *,
    unit: VocabUnit | None = None,
    reviews: list[dict[str, int]] | None = None,
    tags: list[str] | None = None,
) -> FakeAnki:
    selected = make_unit() if unit is None else unit
    return FakeAnki(
        selected,
        [card_record(101, 0)],
        revlog={"101": [] if reviews is None else reviews},
        tags=tags,
    )


class InvokeClient(AnkiConnectClient):
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _invoke(self, action: str, params: dict[str, Any]) -> Any:
        self.calls.append((action, dict(params)))
        return self.responses.pop(0)


def test_cards_info_is_a_strict_public_read_boundary() -> None:
    rows = [card_record(101, 0)]
    client = InvokeClient(rows)

    assert client.cards_info([101]) == rows
    assert client.calls == [("cardsInfo", {"cards": [101]})]


@pytest.mark.parametrize("result", [None, {}, [None], "bad"])
def test_cards_info_rejects_malformed_response(result: object) -> None:
    with pytest.raises(AnkiResponseError, match="list of card objects"):
        InvokeClient(result).cards_info([101])


def test_verified_note_type_snapshot_returns_the_verified_raw_model() -> None:
    model = valid_model()
    client = InvokeClient([model])

    assert client.verified_note_type_snapshot() == model
    assert client.calls == [
        (
            "findModelsByName",
            {"modelNames": [ANKI_NOTE_TYPE_NAME]},
        )
    ]


@pytest.mark.parametrize("result", [None, {}, "bad", [None]])
def test_verified_note_type_snapshot_rejects_malformed_lookup(
    result: object,
) -> None:
    with pytest.raises(AnkiResponseError, match="list of model objects"):
        InvokeClient(result).verified_note_type_snapshot()


@pytest.mark.parametrize("result", [[], [valid_model(), valid_model()]])
def test_verified_note_type_snapshot_requires_exactly_one_model(
    result: object,
) -> None:
    with pytest.raises(AnkiNoteTypeMismatchError, match="exactly one"):
        InvokeClient(result).verified_note_type_snapshot()


def test_verify_note_type_reuses_verified_snapshot_read() -> None:
    client = InvokeClient([valid_model()])

    assert client.verify_note_type() is True
    assert [action for action, _params in client.calls] == ["findModelsByName"]


def test_runtime_ordinal_maps_through_verified_template_name() -> None:
    model = valid_model({"R": 2, "L": 0, "W": 3, "S": 1})
    anki = FakeAnki(make_unit(), [card_record(101, 2)], model=model)

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog(),
        now=NOW,
    )

    assert progress.channels[0].channel == "R"
    assert progress.channels[0].template_name == "R"
    assert progress.channels[0].template_ordinal == 2


def test_template_response_order_does_not_change_attribution() -> None:
    model = valid_model()
    model["tmpls"] = list(reversed(model["tmpls"]))
    anki = FakeAnki(make_unit(), [card_record(101, 0)], model=model)

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog(),
        now=NOW,
    )

    assert tuple(channel.channel for channel in progress.channels) == ("R",)


def test_enabled_channel_missing_card_fails_closed() -> None:
    unit = make_unit(states={"R": "NEW", "L": "NEW"})
    anki = FakeAnki(unit, [card_record(101, 0)])

    with pytest.raises(ReconcileCardError, match="enabled channels"):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


def test_disabled_channel_card_fails_closed() -> None:
    anki = FakeAnki(
        make_unit(),
        [card_record(101, 0), card_record(102, 1)],
    )

    with pytest.raises(ReconcileCardError, match="disabled channel L"):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


def test_duplicate_channel_cards_fail_closed() -> None:
    anki = FakeAnki(
        make_unit(),
        [card_record(101, 0), card_record(102, 0)],
    )

    with pytest.raises(ReconcileCardError, match="multiple cards"):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


def test_foreign_note_card_fails_closed() -> None:
    anki = FakeAnki(make_unit(), [card_record(101, 0, note_id=9999)])

    with pytest.raises(ReconcileCardError, match="another note"):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"cardId": "101"},
        {"ord": -1},
        {"interval": -1},
        {"lapses": -1},
        {"queue": False},
    ],
    ids=["card-id", "ordinal", "interval", "lapses", "queue"],
)
def test_malformed_cards_info_fails_closed(change: dict[str, object]) -> None:
    card = card_record(101, 0)
    card.update(change)
    anki = FakeAnki(make_unit(), [card], note_cards=[101])

    with pytest.raises(ReconcileCardError):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


@pytest.mark.parametrize(
    ("queue", "expected"),
    [
        (-1, True),
        (-2, False),
        (-3, False),
        (0, False),
    ],
    ids=["suspended", "user-buried", "scheduler-buried", "active"],
)
def test_only_queue_minus_one_is_suspension(queue: int, expected: bool) -> None:
    anki = FakeAnki(
        make_unit(),
        [card_record(101, 0, interval=42, lapses=4, queue=queue)],
    )

    channel = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert channel.interval_days == 42
    assert channel.lapses_total == 4
    assert channel.is_suspended is expected


def test_no_review_has_zero_age_and_no_lifecycle_ids() -> None:
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.age_days == 0
    assert progress.lapses_last_30_days == 0
    assert progress.first_lifecycle_review_id is None
    assert progress.latest_lifecycle_review_id is None
    assert progress.latest_lapse_review_id is None
    assert progress.state_entered_at == ""
    assert progress.first_lifecycle_review_after_state_entry_id is None
    assert progress.first_lapse_after_state_entry_id is None


def test_initial_new_has_no_post_entry_evidence_without_a_commit() -> None:
    lifecycle_review = review(
        NOW - timedelta(days=1),
        review_type=1,
        ease=1,
    )

    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[lifecycle_review]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.state == "NEW"
    assert progress.state_entered_at == ""
    assert progress.first_lifecycle_review_after_state_entry_id is None
    assert progress.first_lapse_after_state_entry_id is None


@pytest.mark.parametrize("review_type", [0, 2], ids=["learning", "relearning"])
def test_non_review_again_is_not_a_lifecycle_lapse(review_type: int) -> None:
    item = review(
        NOW - timedelta(days=5),
        review_type=review_type,
        ease=1,
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[item]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.first_lifecycle_review_id == item["id"]
    assert progress.latest_lapse_review_id is None
    assert progress.lapses_last_30_days == 0


def test_review_again_is_a_lifecycle_lapse() -> None:
    item = review(NOW - timedelta(days=5), review_type=1, ease=1)
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[item]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.latest_lapse_review_id == item["id"]
    assert progress.lapses_last_30_days == 1


def test_cram_is_ignored_as_lifecycle_evidence() -> None:
    item = review(NOW - timedelta(days=5), review_type=3, ease=1)
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[item]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.first_lifecycle_review_id is None
    assert progress.latest_lapse_review_id is None
    assert progress.age_days == 0


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (
            timedelta(
                seconds=(
                    STABLE_ZERO_LAPSE_WINDOW_DAYS * LIFECYCLE_SECONDS_PER_DAY
                )
            ),
            1,
        ),
        (
            timedelta(
                seconds=(
                    STABLE_ZERO_LAPSE_WINDOW_DAYS * LIFECYCLE_SECONDS_PER_DAY
                ),
                milliseconds=1,
            ),
            0,
        ),
    ],
    ids=["exact-boundary-inclusive", "one-ms-outside"],
)
def test_lapse_window_uses_exact_contract_boundary(
    age: timedelta,
    expected: int,
) -> None:
    item = review(NOW - age, review_type=1, ease=1)
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[item]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.lapses_last_30_days == expected


def test_lapse_window_is_derived_from_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reconcile_module, "STABLE_ZERO_LAPSE_WINDOW_DAYS", 1)
    included = review(NOW - timedelta(days=1), review_type=1, ease=1)
    excluded = review(
        NOW - timedelta(days=1, milliseconds=1),
        review_type=1,
        ease=1,
    )

    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[included, excluded]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.lapses_last_30_days == 1


def test_future_revlog_id_fails_closed() -> None:
    item = review(NOW + timedelta(milliseconds=1), review_type=1, ease=3)

    with pytest.raises(ReconcileRevlogError, match="future"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(reviews=[item]),
            event_log=FakeEventLog(),
            now=NOW,
        )


def test_revlog_is_sorted_deterministically_by_id() -> None:
    first = review(NOW - timedelta(days=10), review_type=0, ease=3)
    middle = review(NOW - timedelta(days=5), review_type=1, ease=1)
    latest = review(NOW - timedelta(days=1), review_type=1, ease=3)
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(reviews=[latest, first, middle]),
        event_log=FakeEventLog(),
        now=NOW,
    ).channels[0]

    assert progress.first_lifecycle_review_id == first["id"]
    assert progress.latest_lifecycle_review_id == latest["id"]
    assert progress.latest_lapse_review_id == middle["id"]
    assert progress.age_days == 10


def test_duplicate_revlog_id_fails_closed() -> None:
    item = review(NOW - timedelta(days=1), review_type=1, ease=3)

    with pytest.raises(ReconcileRevlogError, match="duplicate revlog id"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(reviews=[item, dict(item)]),
            event_log=FakeEventLog(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "revlog",
    [
        {},
        {"101": [], "999": []},
        {"101": [{"id": 1, "ease": 3, "type": 1}]},
    ],
    ids=["missing-card", "unexpected-card", "missing-ivl"],
)
def test_revlog_shape_mismatch_fails_closed(
    revlog: dict[str, list[dict[str, int]]],
) -> None:
    anki = FakeAnki(
        make_unit(),
        [card_record(101, 0)],
        revlog=revlog,
    )

    with pytest.raises(ReconcileRevlogError):
        observe_unit(NOTE_ID, anki=anki, event_log=FakeEventLog(), now=NOW)


def test_historical_judge_without_d35_fields_is_readable_but_not_eligible() -> None:
    event = stored_event(
        "JUDGE",
        {
            "channel": "R",
            "passed": True,
            "model_id": "human",
            "model_version": "1",
        },
        ts=NOW - timedelta(days=2),
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog([event]),
        now=NOW,
    ).channels[0]

    assert progress.assessments == ()


def test_valid_lifecycle_judges_become_timestamp_ordered_assessments() -> None:
    later = stored_event(
        "JUDGE",
        {
            "channel": "R",
            "passed": False,
            "assessment_id": "assessment-2",
            "stimulus_ref": "stimulus-2",
            "novel": True,
            "model_id": "human",
            "model_version": "1",
        },
        ts=NOW - timedelta(days=1),
    )
    earlier = stored_event(
        "JUDGE",
        {
            "channel": "R",
            "passed": True,
            "assessment_id": "assessment-1",
            "stimulus_ref": "stimulus-1",
            "novel": True,
            "model_id": "human",
            "model_version": "1",
        },
        ts=NOW - timedelta(days=2),
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog([later, earlier]),
        now=NOW,
    ).channels[0]

    assert tuple(item.assessment_id for item in progress.assessments) == (
        "assessment-1",
        "assessment-2",
    )
    assert progress.assessments[0].passed is True
    assert progress.assessments[1].passed is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "channel": "R",
            "passed": True,
            "assessment_id": "assessment-1",
            "model_id": "human",
            "model_version": "1",
        },
        {
            "channel": "R",
            "passed": "yes",
            "assessment_id": "assessment-1",
            "stimulus_ref": "stimulus-1",
            "novel": True,
            "model_id": "human",
            "model_version": "1",
        },
    ],
    ids=["partial-d35", "passed-not-bool"],
)
def test_malformed_lifecycle_judge_fails_closed(
    payload: dict[str, object],
) -> None:
    event = stored_event("JUDGE", payload, ts=NOW - timedelta(days=1))

    with pytest.raises(ReconcileEventHistoryError, match="JUDGE"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog([event]),
            now=NOW,
        )


def test_prepare_only_is_valid_incomplete_and_does_not_advance_state() -> None:
    events, _transition_id = journal_transition(phases=("PREPARE",))

    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert progress.state == "NEW"
    assert progress.state_episode_id == initial_episode_id("R")


def test_prepare_then_abort_is_valid_and_does_not_advance_state() -> None:
    events, _transition_id = journal_transition(phases=("PREPARE", "ABORT"))

    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert progress.state == "NEW"
    assert progress.state_episode_id == initial_episode_id("R")


@pytest.mark.parametrize("phase", ["COMMIT", "ABORT"])
def test_terminal_without_prepare_fails_closed(phase: str) -> None:
    events, _transition_id = journal_transition(phases=(phase,))

    with pytest.raises(ReconcileEventHistoryError, match="requires PREPARE"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog(events),
            now=NOW,
        )


def test_terminal_before_prepare_fails_closed() -> None:
    events, _transition_id = journal_transition(phases=("COMMIT", "PREPARE"))

    with pytest.raises(ReconcileEventHistoryError, match="requires PREPARE"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog(events),
            now=NOW,
        )


def test_commit_establishes_current_state_episode() -> None:
    unit = make_unit(states={"R": "LEARNING"})
    state_entry = NOW - timedelta(days=1)
    events, transition_id = journal_transition(terminal_ts=state_entry)
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(unit=unit),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert progress.state == "LEARNING"
    assert progress.state_episode_id == transition_id
    assert progress.state_entered_at == state_entry.isoformat()


def test_earliest_lifecycle_review_strictly_after_state_entry_is_stable() -> None:
    state_entry = NOW - timedelta(days=1)
    before = review(
        state_entry - timedelta(seconds=1),
        review_type=0,
        ease=3,
    )
    equal = review(state_entry, review_type=1, ease=3)
    earliest_after = review(
        state_entry + timedelta(seconds=3),
        review_type=0,
        ease=3,
    )
    later = review(
        state_entry + timedelta(seconds=5),
        review_type=2,
        ease=3,
    )
    events, _transition_id = journal_transition(terminal_ts=state_entry)
    unit = make_unit(states={"R": "LEARNING"})

    sorted_progress = observe_unit(
        NOTE_ID,
        anki=default_anki(
            unit=unit,
            reviews=[before, equal, earliest_after, later],
        ),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]
    unsorted_progress = observe_unit(
        NOTE_ID,
        anki=default_anki(
            unit=unit,
            reviews=[later, earliest_after, before, equal],
        ),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert sorted_progress.state_entered_at == state_entry.isoformat()
    assert (
        sorted_progress.first_lifecycle_review_after_state_entry_id
        == earliest_after["id"]
    )
    assert (
        unsorted_progress.first_lifecycle_review_after_state_entry_id
        == earliest_after["id"]
    )


def test_earliest_review_lapse_strictly_after_state_entry_is_stable() -> None:
    state_entry = NOW - timedelta(days=1)
    before = review(
        state_entry - timedelta(seconds=1),
        review_type=1,
        ease=1,
    )
    equal = review(state_entry, review_type=1, ease=1)
    learning_again = review(
        state_entry + timedelta(seconds=1),
        review_type=0,
        ease=1,
    )
    relearning_again = review(
        state_entry + timedelta(seconds=2),
        review_type=2,
        ease=1,
    )
    earliest_lapse = review(
        state_entry + timedelta(seconds=3),
        review_type=1,
        ease=1,
    )
    later_lapse = review(
        state_entry + timedelta(seconds=5),
        review_type=1,
        ease=1,
    )
    chronological = [
        before,
        equal,
        learning_again,
        relearning_again,
        earliest_lapse,
        later_lapse,
    ]
    unsorted = [
        later_lapse,
        relearning_again,
        before,
        earliest_lapse,
        equal,
        learning_again,
    ]
    events, _transition_id = journal_transition(terminal_ts=state_entry)
    unit = make_unit(states={"R": "LEARNING"})

    sorted_progress = observe_unit(
        NOTE_ID,
        anki=default_anki(unit=unit, reviews=chronological),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]
    unsorted_progress = observe_unit(
        NOTE_ID,
        anki=default_anki(unit=unit, reviews=unsorted),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert (
        sorted_progress.first_lifecycle_review_after_state_entry_id
        == learning_again["id"]
    )
    assert sorted_progress.first_lapse_after_state_entry_id == earliest_lapse["id"]
    assert unsorted_progress.first_lapse_after_state_entry_id == earliest_lapse["id"]


def test_persisted_non_new_state_without_provenance_fails_closed() -> None:
    unit = make_unit(states={"R": "STABLE"})

    with pytest.raises(ReconcileEventHistoryError, match="reconstructed"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=unit),
            event_log=FakeEventLog(),
            now=NOW,
        )


def test_historical_pre_d38_state_does_not_invent_episode_provenance() -> None:
    unit = make_unit(states={"R": "LEARNING"})
    historical = stored_event(
        "STATE",
        {
            "channel": "R",
            "from": "NEW",
            "to": "LEARNING",
            "trigger": "historical",
            "evidence": {"legacy": True},
        },
        ts=NOW - timedelta(days=1),
    )

    with pytest.raises(ReconcileEventHistoryError, match="reconstructed"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=unit),
            event_log=FakeEventLog([historical]),
            now=NOW,
        )


def test_initial_new_episode_identity_is_deterministic() -> None:
    anki = default_anki()
    event_log = FakeEventLog()

    first = observe_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)
    second = observe_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)

    first_id = first.channels[0].state_episode_id
    assert first_id == second.channels[0].state_episode_id
    assert first_id == INITIAL_NEW_EPISODE_PREFIX + sha256(
        b'{"channel":"R","unit_key":"subtle::small-difference"}'
    ).hexdigest()

    listening = observe_unit(
        NOTE_ID,
        anki=FakeAnki(
            make_unit(states={"L": "NEW"}),
            [card_record(101, 1)],
        ),
        event_log=FakeEventLog(),
        now=NOW,
    )
    assert listening.channels[0].state_episode_id != first_id


def test_wrong_full_hex_transition_digest_fails_closed() -> None:
    payload = state_payload()
    transition_id = str(payload["transition_id"])
    replacement = "0" if transition_id[0] != "0" else "1"
    payload["transition_id"] = replacement + transition_id[1:]
    event = stored_event("STATE", payload, ts=NOW - timedelta(days=1))

    with pytest.raises(ReconcileEventHistoryError, match="canonical identity"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog([event]),
            now=NOW,
        )


def test_missing_from_episode_id_fails_closed() -> None:
    payload = state_payload()
    del payload["from_episode_id"]
    event = stored_event("STATE", payload, ts=NOW - timedelta(days=1))

    with pytest.raises(ReconcileEventHistoryError, match="missing"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog([event]),
            now=NOW,
        )


@pytest.mark.parametrize("changed_field", ["evidence", "from_episode_id"])
def test_transition_identity_cannot_change_across_phases(
    changed_field: str,
) -> None:
    prepare = state_payload()
    terminal = dict(prepare)
    terminal["phase"] = "COMMIT"
    if changed_field == "evidence":
        terminal["evidence"] = {"changed": True}
    else:
        terminal["from_episode_id"] = initial_episode_id("L")
    events = [
        stored_event("STATE", prepare, ts=NOW - timedelta(days=1, seconds=1)),
        stored_event("STATE", terminal, ts=NOW - timedelta(days=1)),
    ]

    with pytest.raises(ReconcileEventHistoryError, match="canonical identity"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog(events),
            now=NOW,
        )


@pytest.mark.parametrize(
    "phases",
    [
        ("PREPARE", "PREPARE"),
        ("PREPARE", "COMMIT", "COMMIT"),
        ("PREPARE", "ABORT", "ABORT"),
    ],
    ids=["prepare", "commit", "abort"],
)
def test_duplicate_journal_phase_fails_closed(phases: tuple[str, ...]) -> None:
    events, _transition_id = journal_transition(phases=phases)

    with pytest.raises(ReconcileEventHistoryError, match="duplicates phase"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog(events),
            now=NOW,
        )


def test_commit_and_abort_for_one_transition_fail_closed() -> None:
    events, _transition_id = journal_transition(
        phases=("PREPARE", "COMMIT", "ABORT")
    )

    with pytest.raises(ReconcileEventHistoryError, match="COMMIT and ABORT"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog(events),
            now=NOW,
        )


def test_present_transition_group_id_must_be_full_lower_hex() -> None:
    payload = state_payload(transition_group_id="DORMANCY")
    event = stored_event("STATE", payload, ts=NOW - timedelta(days=1))

    with pytest.raises(ReconcileEventHistoryError, match="transition_group_id"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=FakeEventLog([event]),
            now=NOW,
        )


def test_broken_intermediate_state_chain_fails_closed() -> None:
    first, first_id = journal_transition()
    broken, _broken_id = journal_transition(
        from_state="STABLE",
        to_state="MASTERED",
        trigger="MASTERY_ASSESSMENT_PASS",
        from_episode_id=first_id,
        terminal_ts=NOW - timedelta(hours=12),
    )

    with pytest.raises(ReconcileEventHistoryError, match="lifecycle chain"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=make_unit(states={"R": "MASTERED"})),
            event_log=FakeEventLog([*first, *broken]),
            now=NOW,
        )


def test_broken_intermediate_episode_chain_fails_closed() -> None:
    first, _first_id = journal_transition()
    broken, _broken_id = journal_transition(
        from_state="LEARNING",
        to_state="STABLE",
        trigger="STABILITY_GATE",
        from_episode_id="f" * 64,
        terminal_ts=NOW - timedelta(hours=12),
    )

    with pytest.raises(ReconcileEventHistoryError, match="episode provenance"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=make_unit(states={"R": "STABLE"})),
            event_log=FakeEventLog([*first, *broken]),
            now=NOW,
        )


def test_final_persisted_state_mismatch_fails_closed() -> None:
    events, _transition_id = journal_transition()

    with pytest.raises(ReconcileEventHistoryError, match="reconstructed"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=make_unit(states={"R": "STABLE"})),
            event_log=FakeEventLog(events),
            now=NOW,
        )


def test_multiple_committed_transitions_reconstruct_verified_chain() -> None:
    first_terminal = NOW - timedelta(days=3)
    events, latest_transition_id = committed_chain(
        "R",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
        ),
        first_terminal_ts=first_terminal,
    )

    channel = observe_unit(
        NOTE_ID,
        anki=default_anki(unit=make_unit(states={"R": "MASTERED"})),
        event_log=FakeEventLog(events),
        now=NOW,
    ).channels[0]

    assert channel.state == "MASTERED"
    assert channel.state_episode_id == latest_transition_id
    assert channel.state_entered_at == (
        first_terminal + timedelta(seconds=2)
    ).isoformat()


def test_all_mastered_timestamp_uses_latest_committed_entry() -> None:
    unit = make_unit(states={"R": "MASTERED", "L": "MASTERED"})
    anki = FakeAnki(
        unit,
        [card_record(101, 0), card_record(102, 1)],
    )
    first_entry = NOW - timedelta(days=12)
    latest_entry = NOW - timedelta(days=10)
    r_events, _r_latest = committed_chain(
        "R",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
        ),
        first_terminal_ts=first_entry - timedelta(seconds=2),
    )
    l_events, _l_latest = committed_chain(
        "L",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
        ),
        first_terminal_ts=latest_entry - timedelta(seconds=2),
    )

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog([*r_events, *l_events]),
        now=NOW,
    )

    assert progress.all_active_channels_mastered_at == latest_entry.isoformat()


def test_one_non_mastered_channel_keeps_unit_mastered_timestamp_empty() -> None:
    unit = make_unit(states={"R": "MASTERED", "L": "STABLE"})
    anki = FakeAnki(
        unit,
        [card_record(101, 0), card_record(102, 1)],
    )
    r_events, _r_latest = committed_chain(
        "R",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
            ("STABLE", "MASTERED", "MASTERY_ASSESSMENT_PASS"),
        ),
        first_terminal_ts=NOW - timedelta(days=10, seconds=2),
    )
    l_events, _l_latest = committed_chain(
        "L",
        (
            ("NEW", "LEARNING", "FIRST_REVIEW"),
            ("LEARNING", "STABLE", "STABILITY_GATE"),
        ),
        first_terminal_ts=NOW - timedelta(days=5, seconds=1),
    )

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog([*r_events, *l_events]),
        now=NOW,
    )

    assert progress.all_active_channels_mastered_at == ""


def test_leech_tag_changes_only_unit_diagnostic_flag() -> None:
    plain = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=FakeEventLog(),
        now=NOW,
    )
    leech = observe_unit(
        NOTE_ID,
        anki=default_anki(tags=["leech"]),
        event_log=FakeEventLog(),
        now=NOW,
    )

    assert plain.has_leech_tag is False
    assert leech.has_leech_tag is True
    assert tuple(item.state for item in plain.channels) == ("NEW",)
    assert tuple(item.state for item in leech.channels) == ("NEW",)
    assert plain.channels == leech.channels


def test_observation_performs_zero_anki_or_eventlog_writes() -> None:
    anki = default_anki()
    event_log = FakeEventLog()

    observe_unit(NOTE_ID, anki=anki, event_log=event_log, now=NOW)

    assert anki.mutation_calls == []
    assert event_log.log_calls == []
    assert event_log.read_calls == 1
