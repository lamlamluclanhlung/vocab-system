"""Tests for the append-only JSONL event log."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import vocab.events as events_module
from vocab.contracts import (
    EVENT_PAYLOAD_REQUIRED_FIELDS,
    RESERVED_EVENT_TYPES,
)
from vocab.events import (
    EventLog,
    EventLogCorruptionError,
    EventLogCorruptionWarning,
    UnsupportedEventVersionError,
)


UNIT_KEY = "subtle::small-difference"


def valid_payload(event_type: str, **overrides: object) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {
        "FORGE": {
            "source_ref": "dictionary:test:subtle",
            "accepted": True,
        },
        "JUDGE": {
            "channel": "R",
            "passed": True,
            "model_id": "test-judge",
            "model_version": "1",
        },
        "STATE": {
            "channel": "R",
            "from": "NEW",
            "to": "LEARNING",
            "trigger": "test-trigger",
        },
        "SPEAK": {
            "audio_path": "test-audio.wav",
            "transcript": "test transcript",
            "passed": True,
            "model_id": "test-speech",
            "model_version": "1",
        },
        "ENCOUNTER": {
            "count": 1,
            "source": "test-corpus",
            "month": "2026-08",
        },
    }
    payload = payloads[event_type].copy()
    payload.update(overrides)
    return payload


_REQUIRED_EMITTED_PAYLOAD_FIELDS = tuple(
    (event_type, field_name)
    for event_type, field_names in EVENT_PAYLOAD_REQUIRED_FIELDS.items()
    if event_type not in RESERVED_EVENT_TYPES
    for field_name in field_names
)


def set_clock(monkeypatch: pytest.MonkeyPatch, *instants: datetime) -> None:
    clock = iter(instants)
    monkeypatch.setattr(events_module, "_now_utc", lambda: next(clock))


def test_missing_file_is_created(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    EventLog(path)

    assert path.is_file()


def test_append_and_read_preserves_order(tmp_path, monkeypatch) -> None:
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 3, tzinfo=timezone.utc),
    )
    log = EventLog(tmp_path / "events.jsonl")

    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", rating=3))
    log.log("FORGE", UNIT_KEY, valid_payload("FORGE", source="dictionary"))
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", count=2))

    assert [event.event for event in log.read()] == [
        "ENCOUNTER",
        "FORGE",
        "ENCOUNTER",
    ]


def test_reopening_and_appending_preserves_existing_records(tmp_path, monkeypatch) -> None:
    path = tmp_path / "events.jsonl"
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
    )

    first = EventLog(path)
    first.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", sequence=1))
    before = path.read_bytes()
    EventLog(path).log(
        "ENCOUNTER",
        UNIT_KEY,
        valid_payload("ENCOUNTER", sequence=2),
    )

    assert path.read_bytes().startswith(before)
    assert [event.payload["sequence"] for event in EventLog(path).read()] == [1, 2]


def test_vietnamese_payload_is_readable_utf8(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"

    EventLog(path).log(
        "FORGE",
        UNIT_KEY,
        valid_payload("FORGE", text="tiếng Việt rất đẹp"),
    )

    stored = path.read_text(encoding="utf-8")
    assert "tiếng Việt rất đẹp" in stored
    assert "\\u" not in stored


def test_event_type_filtering(tmp_path, monkeypatch) -> None:
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 3, tzinfo=timezone.utc),
    )
    log = EventLog(tmp_path / "events.jsonl")
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    log.log("FORGE", UNIT_KEY, valid_payload("FORGE"))
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))

    assert [event.event for event in log.read(event_type="ENCOUNTER")] == [
        "ENCOUNTER",
        "ENCOUNTER",
    ]


def test_since_filter_is_inclusive_and_normalizes_offsets(tmp_path, monkeypatch) -> None:
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 3, tzinfo=timezone.utc),
    )
    log = EventLog(tmp_path / "events.jsonl")
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", sequence=1))
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", sequence=2))
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER", sequence=3))

    inclusive = log.read(since="2026-08-18T02:00:00+00:00")
    offset = log.read(since="2026-08-18T09:00:00+07:00")

    assert [event.payload["sequence"] for event in inclusive] == [2, 3]
    assert [event.payload["sequence"] for event in offset] == [2, 3]


def test_naive_since_is_rejected(tmp_path) -> None:
    log = EventLog(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="explicit timezone offset"):
        log.read(since="2026-08-18T02:00:00")


def test_stored_timestamp_is_utc_and_day_is_ho_chi_minh(tmp_path, monkeypatch) -> None:
    # 17:30 UTC is 00:30 on the following local calendar day (UTC+7).
    set_clock(monkeypatch, datetime(2026, 8, 18, 17, 30, tzinfo=timezone.utc))

    event = EventLog(tmp_path / "events.jsonl").log(
        "ENCOUNTER",
        UNIT_KEY,
        valid_payload("ENCOUNTER"),
    )

    assert event.ts == "2026-08-18T17:30:00+00:00"
    assert event.day == "2026-08-19"


@pytest.mark.parametrize("event_type", ["UNKNOWN", "", None])
def test_invalid_event_type_is_rejected(tmp_path, event_type) -> None:
    log = EventLog(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="event type"):
        log.log(event_type, UNIT_KEY, {})


def test_non_dict_payload_is_rejected(tmp_path) -> None:
    with pytest.raises(TypeError, match="payload must be a dict"):
        EventLog(tmp_path / "events.jsonl").log("ENCOUNTER", UNIT_KEY, [])


@pytest.mark.parametrize("event_type", ["JUDGE", "SPEAK"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"model_id": "judge"},
        {"model_id": "", "model_version": "1"},
        {"model_id": "judge", "model_version": ""},
    ],
)
def test_model_events_require_nonempty_metadata(tmp_path, event_type, payload) -> None:
    with pytest.raises(ValueError, match="requires non-empty"):
        EventLog(tmp_path / "events.jsonl").log(event_type, UNIT_KEY, payload)


@pytest.mark.parametrize("payload", [{}, {"channel": ""}, {"channel": "X"}])
def test_state_requires_a_valid_channel(tmp_path, payload) -> None:
    with pytest.raises(ValueError, match="channel"):
        EventLog(tmp_path / "events.jsonl").log("STATE", UNIT_KEY, payload)


@pytest.mark.parametrize(
    ("event_type", "missing_field"),
    _REQUIRED_EMITTED_PAYLOAD_FIELDS,
)
def test_emitted_event_requires_every_contract_payload_field(
    tmp_path,
    event_type,
    missing_field,
) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    payload = valid_payload(event_type)
    payload.pop(missing_field)
    before = path.read_bytes()

    with pytest.raises(ValueError) as captured:
        log.log(event_type, UNIT_KEY, payload)

    message = str(captured.value)
    assert event_type in message
    assert missing_field in message
    assert path.read_bytes() == before


def test_reserved_review_cannot_be_emitted(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="reserved"):
        log.log("REVIEW", UNIT_KEY, {})

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "non_finite",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_non_finite_json_number_is_rejected_before_append(
    tmp_path,
    non_finite,
) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    payload = valid_payload(
        "ENCOUNTER",
        diagnostic_score=non_finite,
    )
    before = path.read_bytes()

    with pytest.raises(ValueError):
        log.log("ENCOUNTER", UNIT_KEY, payload)

    assert path.read_bytes() == before


def test_historical_review_remains_readable(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    record = {
        "v": events_module.EVENT_SCHEMA_VERSION,
        "ts": "2026-08-18T01:00:00+00:00",
        "day": "2026-08-18",
        "event": "REVIEW",
        "unit_key": UNIT_KEY,
        "payload": {"rating": 3},
    }
    path.write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )

    result = EventLog(path).read(event_type="REVIEW")

    assert len(result) == 1
    assert result[0].event == "REVIEW"
    assert result[0].payload == {"rating": 3}


def test_malformed_final_record_is_ignored_with_warning(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    valid = log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"v": 1')

    with pytest.warns(EventLogCorruptionWarning, match="final"):
        result = log.read()

    assert result == [valid]


def test_malformed_json_in_middle_raises_corruption(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    valid_line = path.read_text(encoding="utf-8")
    path.write_text(valid_line + "not json\n" + valid_line, encoding="utf-8")

    with pytest.raises(EventLogCorruptionError, match="line 2"):
        log.read()


def test_truncated_utf8_in_final_record_is_ignored_with_warning(
    tmp_path, monkeypatch
) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    valid = log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    truncated_character = "ế".encode("utf-8")[:-1]
    with path.open("ab") as handle:
        handle.write(b'{"payload": "ti' + truncated_character)

    with pytest.warns(EventLogCorruptionWarning, match="final"):
        result = log.read()

    assert result == [valid]


def test_invalid_utf8_in_middle_raises_corruption(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    valid_line = path.read_bytes()
    invalid_record = b'{"payload": "' + "ế".encode("utf-8")[:-1] + b'"}\n'
    path.write_bytes(valid_line + invalid_record + valid_line)

    with pytest.raises(EventLogCorruptionError, match="UTF-8.*line 2"):
        log.read()


def test_append_refuses_malformed_trailing_record_without_modifying_file(
    tmp_path, monkeypatch
) -> None:
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
    )
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"v": 1')
    before = path.read_bytes()

    with pytest.raises(EventLogCorruptionError, match="refusing to append"):
        log.log("FORGE", UNIT_KEY, valid_payload("FORGE"))

    assert path.read_bytes() == before


def test_append_refuses_invalid_utf8_tail_without_modifying_file(
    tmp_path, monkeypatch
) -> None:
    set_clock(
        monkeypatch,
        datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
    )
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    invalid_record = b'{"payload": "' + "ế".encode("utf-8")[:-1] + b'"}\n'
    with path.open("ab") as handle:
        handle.write(invalid_record)
    before = path.read_bytes()

    with pytest.raises(EventLogCorruptionError, match="refusing to append"):
        log.log("FORGE", UNIT_KEY, valid_payload("FORGE"))

    assert path.read_bytes() == before


def test_append_parses_only_the_trailing_record(tmp_path, monkeypatch) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    valid_record = {
        "v": 1,
        "ts": "2026-08-18T01:00:00+00:00",
        "day": "2026-08-18",
        "event": "REVIEW",
        "unit_key": UNIT_KEY,
        "payload": {},
    }
    # Invalid historical JSON is deliberately outside the append-time tail
    # contract. A full-history validation would reject it or parse it.
    path.write_text(
        "malformed historical record\n" + json.dumps(valid_record) + "\n",
        encoding="utf-8",
    )
    real_loads = events_module.json.loads
    parsed_records = []

    def counting_loads(value):
        parsed_records.append(value)
        return real_loads(value)

    monkeypatch.setattr(events_module.json, "loads", counting_loads)
    set_clock(monkeypatch, datetime(2026, 8, 18, 2, tzinfo=timezone.utc))

    log.log("FORGE", UNIT_KEY, valid_payload("FORGE"))

    assert len(parsed_records) == 1
    assert parsed_records[0].rstrip("\r") == json.dumps(valid_record)


def test_additive_envelope_fields_are_accepted(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    expected = log.log(
        "ENCOUNTER",
        UNIT_KEY,
        valid_payload("ENCOUNTER", rating=3),
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    record["trace_id"] = "trace-123"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert log.read() == [expected]


def test_future_schema_version_raises_dedicated_error(tmp_path, monkeypatch) -> None:
    set_clock(monkeypatch, datetime(2026, 8, 18, tzinfo=timezone.utc))
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("ENCOUNTER", UNIT_KEY, valid_payload("ENCOUNTER"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["v"] = events_module.EVENT_SCHEMA_VERSION + 1
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(UnsupportedEventVersionError) as captured:
        log.read()

    assert captured.value.version == events_module.EVENT_SCHEMA_VERSION + 1
    assert not isinstance(captured.value, EventLogCorruptionError)


def test_syntactically_valid_invalid_envelope_fails_closed(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    EventLog(path)
    path.write_text(json.dumps({"event": "REVIEW"}) + "\n", encoding="utf-8")

    with pytest.raises(EventLogCorruptionError, match="invalid event envelope"):
        EventLog(path).read()
