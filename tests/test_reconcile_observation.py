"""Deterministic tests for the read-only T9.1 observation layer."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

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
    NOTE_FIELDS,
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


def state_payload(
    *,
    channel: str = "R",
    from_state: str = "NEW",
    to_state: str = "LEARNING",
    trigger: str = "FIRST_REVIEW",
    transition_id: str = "a" * 64,
    phase: str = "COMMIT",
) -> dict[str, object]:
    return {
        "channel": channel,
        "from": from_state,
        "to": to_state,
        "trigger": trigger,
        "transition_id": transition_id,
        "phase": phase,
        "evidence": {"test": transition_id[0]},
    }


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
        (timedelta(days=30), 1),
        (timedelta(days=30, milliseconds=1), 0),
    ],
    ids=["exact-boundary-inclusive", "one-ms-outside"],
)
def test_lapse_window_uses_exact_elapsed_30_day_boundary(
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


@pytest.mark.parametrize("phase", ["PREPARE", "ABORT"])
def test_non_commit_phase_does_not_establish_state_episode(phase: str) -> None:
    unit = make_unit(states={"R": "LEARNING"})
    event = stored_event(
        "STATE",
        state_payload(phase=phase),
        ts=NOW - timedelta(days=1),
    )

    with pytest.raises(ReconcileEventHistoryError, match="no committed"):
        observe_unit(
            NOTE_ID,
            anki=default_anki(unit=unit),
            event_log=FakeEventLog([event]),
            now=NOW,
        )


def test_commit_establishes_current_state_episode() -> None:
    unit = make_unit(states={"R": "LEARNING"})
    transition_id = "b" * 64
    event = stored_event(
        "STATE",
        state_payload(transition_id=transition_id),
        ts=NOW - timedelta(days=1),
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(unit=unit),
        event_log=FakeEventLog([event]),
        now=NOW,
    ).channels[0]

    assert progress.state == "LEARNING"
    assert progress.state_episode_id == transition_id


def test_persisted_non_new_state_without_provenance_fails_closed() -> None:
    unit = make_unit(states={"R": "STABLE"})

    with pytest.raises(ReconcileEventHistoryError, match="no committed"):
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

    with pytest.raises(ReconcileEventHistoryError, match="no committed"):
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
    assert first_id.startswith("initial-new:")
    assert len(first_id) == len("initial-new:") + 64

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


def test_all_mastered_timestamp_uses_latest_committed_entry() -> None:
    unit = make_unit(states={"R": "MASTERED", "L": "MASTERED"})
    anki = FakeAnki(
        unit,
        [card_record(101, 0), card_record(102, 1)],
    )
    first_entry = NOW - timedelta(days=12)
    latest_entry = NOW - timedelta(days=10)
    events = [
        stored_event(
            "STATE",
            state_payload(
                channel="R",
                from_state="STABLE",
                to_state="MASTERED",
                trigger="MASTERY_ASSESSMENT_PASS",
                transition_id="c" * 64,
            ),
            ts=first_entry,
        ),
        stored_event(
            "STATE",
            state_payload(
                channel="L",
                from_state="STABLE",
                to_state="MASTERED",
                trigger="MASTERY_ASSESSMENT_PASS",
                transition_id="d" * 64,
            ),
            ts=latest_entry,
        ),
    ]

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog(events),
        now=NOW,
    )

    assert progress.all_active_channels_mastered_at == latest_entry.isoformat()


def test_one_non_mastered_channel_keeps_unit_mastered_timestamp_empty() -> None:
    unit = make_unit(states={"R": "MASTERED", "L": "STABLE"})
    anki = FakeAnki(
        unit,
        [card_record(101, 0), card_record(102, 1)],
    )
    events = [
        stored_event(
            "STATE",
            state_payload(
                channel="R",
                from_state="STABLE",
                to_state="MASTERED",
                trigger="MASTERY_ASSESSMENT_PASS",
                transition_id="e" * 64,
            ),
            ts=NOW - timedelta(days=10),
        ),
        stored_event(
            "STATE",
            state_payload(
                channel="L",
                from_state="LEARNING",
                to_state="STABLE",
                trigger="STABILITY_GATE",
                transition_id="f" * 64,
            ),
            ts=NOW - timedelta(days=5),
        ),
    ]

    progress = observe_unit(
        NOTE_ID,
        anki=anki,
        event_log=FakeEventLog(events),
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
