"""Isolated unit tests for the AnkiConnect HTTP client."""

from __future__ import annotations

import base64
import json
import urllib.error
from collections.abc import Iterable
from typing import Any

import pytest

import vocab.anki as anki_module
from vocab.anki import (
    AnkiAPIError,
    AnkiConnectClient,
    AnkiConnectionError,
    AnkiNoteCreationError,
    AnkiNoteTypeMismatchError,
    AnkiResponseError,
)
from vocab.contracts import (
    ANKI_NOTE_TYPE_NAME,
    CARD_TEMPLATE_NAMES,
    IMMUTABLE_NOTE_FIELDS,
    NOTE_FIELDS,
)
from vocab.models import VocabUnit


class FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def response_body(result: object, error: object = None) -> bytes:
    return json.dumps({"result": result, "error": error}).encode("utf-8")


def install_responses(
    monkeypatch: pytest.MonkeyPatch,
    bodies: Iterable[bytes],
) -> list[tuple[Any, float]]:
    remaining = iter(bodies)
    calls: list[tuple[Any, float]] = []

    def fake_urlopen(request, *, timeout):
        calls.append((request, timeout))
        return FakeHTTPResponse(next(remaining))

    monkeypatch.setattr(anki_module.urllib.request, "urlopen", fake_urlopen)
    return calls


def request_envelope(call: tuple[Any, float]) -> dict[str, Any]:
    request, _timeout = call
    return json.loads(request.data.decode("utf-8"))


def make_unit() -> VocabUnit:
    return VocabUnit(
        unit_key="subtle::small-difference",
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        Target_R="1",
        state_R="NEW",
    )


def test_request_uses_api_version_6_and_configured_transport(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body([101])])
    client = AnkiConnectClient("http://anki.test:9999", timeout=2.5)

    assert client.find_notes("deck:Study") == [101]

    envelope = request_envelope(calls[0])
    request, timeout = calls[0]
    assert envelope == {
        "action": "findNotes",
        "version": 6,
        "params": {"query": "deck:Study"},
    }
    assert request.full_url == "http://anki.test:9999"
    assert request.get_method() == "POST"
    assert request.headers["Content-type"] == "application/json; charset=utf-8"
    assert timeout == 2.5


def test_request_json_is_utf8(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body([])])

    AnkiConnectClient().find_notes('"tiếng Việt"')

    request, _timeout = calls[0]
    assert "tiếng Việt" in request.data.decode("utf-8")


def test_successful_result_is_extracted(monkeypatch) -> None:
    install_responses(monkeypatch, [response_body([11, 12])])

    assert AnkiConnectClient().find_notes("tag:vocab") == [11, 12]


def test_connection_failure_raises_instead_of_returning_empty(monkeypatch) -> None:
    cause = urllib.error.URLError("connection refused")

    def fail(*args, **kwargs):
        raise cause

    monkeypatch.setattr(anki_module.urllib.request, "urlopen", fail)

    with pytest.raises(AnkiConnectionError) as captured:
        AnkiConnectClient().find_notes("tag:vocab")

    assert captured.value.action == "findNotes"
    assert captured.value.cause is cause
    assert captured.value.__cause__ is cause


def test_timeout_raises_connection_error(monkeypatch) -> None:
    cause = TimeoutError("timed out")

    def fail(*args, **kwargs):
        raise cause

    monkeypatch.setattr(anki_module.urllib.request, "urlopen", fail)

    with pytest.raises(AnkiConnectionError) as captured:
        AnkiConnectClient(timeout=0.5).find_notes("")

    assert captured.value.cause is cause


def test_anki_error_raises_dedicated_api_error(monkeypatch) -> None:
    install_responses(
        monkeypatch,
        [response_body(None, "collection is not available")],
    )

    with pytest.raises(AnkiAPIError) as captured:
        AnkiConnectClient().find_notes("tag:vocab")

    assert captured.value.action == "findNotes"
    assert captured.value.error == "collection is not available"
    assert captured.value.result is None


def test_malformed_json_raises_response_error(monkeypatch) -> None:
    install_responses(monkeypatch, [b"not JSON"])

    with pytest.raises(AnkiResponseError, match="UTF-8 JSON"):
        AnkiConnectClient().find_notes("tag:vocab")


@pytest.mark.parametrize(
    "body",
    [
        json.dumps([]).encode("utf-8"),
        json.dumps({"result": []}).encode("utf-8"),
        json.dumps({"error": None}).encode("utf-8"),
    ],
)
def test_malformed_response_envelope_raises(monkeypatch, body) -> None:
    install_responses(monkeypatch, [body])

    with pytest.raises(AnkiResponseError, match="response envelope"):
        AnkiConnectClient().find_notes("tag:vocab")


def test_no_retry_occurs_after_failure(monkeypatch) -> None:
    attempts = 0

    def fail(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(anki_module.urllib.request, "urlopen", fail)

    with pytest.raises(AnkiConnectionError):
        AnkiConnectClient().find_notes("tag:vocab")

    assert attempts == 1


def test_find_notes_maps_to_find_notes_action(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body([4])])

    AnkiConnectClient().find_notes("unit_key:subtle")

    assert request_envelope(calls[0])["action"] == "findNotes"
    assert request_envelope(calls[0])["params"] == {
        "query": "unit_key:subtle"
    }


def test_notes_info_maps_note_ids(monkeypatch) -> None:
    info = [{"noteId": 4, "fields": {}}]
    calls = install_responses(monkeypatch, [response_body(info)])

    assert AnkiConnectClient().notes_info([4]) == info
    assert request_envelope(calls[0]) == {
        "action": "notesInfo",
        "version": 6,
        "params": {"notes": [4]},
    }


def test_update_note_fields_maps_explicit_subset(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body(None)])

    result = AnkiConnectClient().update_note_fields(
        17,
        {"Ctx_2": "A novel context."},
    )

    assert result is None
    assert request_envelope(calls[0]) == {
        "action": "updateNoteFields",
        "version": 6,
        "params": {
            "note": {
                "id": 17,
                "fields": {"Ctx_2": "A novel context."},
            }
        },
    }


@pytest.mark.parametrize("field_name", IMMUTABLE_NOTE_FIELDS)
def test_update_note_fields_rejects_immutable_identity_before_http(
    monkeypatch,
    field_name,
) -> None:
    calls = install_responses(monkeypatch, [])

    with pytest.raises(ValueError, match="immutable"):
        AnkiConnectClient().update_note_fields(
            17,
            {field_name: "changed-identity"},
        )

    assert calls == []

def test_suspend_uses_card_ids(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body(True)])

    assert AnkiConnectClient().suspend([201, 202]) is True
    assert request_envelope(calls[0])["params"] == {"cards": [201, 202]}


def test_unsuspend_uses_card_ids(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body(None)])

    assert AnkiConnectClient().unsuspend([201, 202]) is None
    assert request_envelope(calls[0]) == {
        "action": "unsuspend",
        "version": 6,
        "params": {"cards": [201, 202]},
    }


def test_get_revlog_maps_to_get_reviews_of_cards(monkeypatch) -> None:
    reviews = {"201": [{"id": 9001, "ease": 3}]}
    calls = install_responses(monkeypatch, [response_body(reviews)])

    assert AnkiConnectClient().get_revlog([201]) == reviews
    assert request_envelope(calls[0]) == {
        "action": "getReviewsOfCards",
        "version": 6,
        "params": {"cards": [201]},
    }


def test_store_media_file_encodes_bytes_and_disables_overwrite(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body("actual-name.mp3")])
    raw = b"\x00audio\xff"

    actual = AnkiConnectClient().store_media_file("requested.mp3", raw)

    assert actual == "actual-name.mp3"
    assert request_envelope(calls[0]) == {
        "action": "storeMediaFile",
        "version": 6,
        "params": {
            "filename": "requested.mp3",
            "data": base64.b64encode(raw).decode("ascii"),
            "deleteExisting": False,
        },
    }


def test_add_notes_uses_vocab_note_type_and_runtime_deck(monkeypatch) -> None:
    calls = install_responses(monkeypatch, [response_body([501])])
    unit = make_unit()

    assert AnkiConnectClient().add_notes("Runtime Deck", [unit]) == [501]

    note = request_envelope(calls[0])["params"]["notes"][0]
    assert note["deckName"] == "Runtime Deck"
    assert note["modelName"] == ANKI_NOTE_TYPE_NAME
    assert tuple(note["fields"]) == NOTE_FIELDS
    assert note["fields"] == unit.to_note_fields()
    assert note["options"]["allowDuplicate"] is False


@pytest.mark.parametrize("result", [[501, None], [None], [], "bad result"])
def test_add_notes_fails_closed_for_partial_or_failed_creation(
    monkeypatch,
    result,
) -> None:
    install_responses(monkeypatch, [response_body(result)])

    with pytest.raises(AnkiNoteCreationError) as captured:
        AnkiConnectClient().add_notes("Runtime Deck", [make_unit(), make_unit()])

    assert captured.value.expected_count == 2
    assert captured.value.result == result


def install_valid_note_type(monkeypatch, templates=None):
    if templates is None:
        templates = {name: {} for name in CARD_TEMPLATE_NAMES}
    return install_responses(
        monkeypatch,
        [response_body(list(NOTE_FIELDS)), response_body(templates)],
    )


def test_verify_note_type_accepts_frozen_contract(monkeypatch) -> None:
    calls = install_valid_note_type(monkeypatch)

    assert AnkiConnectClient().verify_note_type() is True
    assert [request_envelope(call)["action"] for call in calls] == [
        "modelFieldNames",
        "modelTemplates",
    ]
    assert all(
        request_envelope(call)["params"]
        == {"modelName": ANKI_NOTE_TYPE_NAME}
        for call in calls
    )


@pytest.mark.parametrize(
    "fields",
    [
        list(NOTE_FIELDS[:-1]),
        [*NOTE_FIELDS, "unexpected"],
        [NOTE_FIELDS[1], NOTE_FIELDS[0], *NOTE_FIELDS[2:]],
    ],
    ids=["missing", "extra", "reordered"],
)
def test_verify_note_type_rejects_field_mismatch(monkeypatch, fields) -> None:
    install_responses(monkeypatch, [response_body(fields)])

    with pytest.raises(AnkiNoteTypeMismatchError, match="field order"):
        AnkiConnectClient().verify_note_type()


@pytest.mark.parametrize(
    "templates",
    [
        {name: {} for name in CARD_TEMPLATE_NAMES[:-1]},
        {**{name: {} for name in CARD_TEMPLATE_NAMES}, "Extra": {}},
    ],
    ids=["missing", "extra"],
)
def test_verify_note_type_rejects_template_name_mismatch(
    monkeypatch,
    templates,
) -> None:
    install_responses(
        monkeypatch,
        [response_body(list(NOTE_FIELDS)), response_body(templates)],
    )

    with pytest.raises(AnkiNoteTypeMismatchError, match="template names"):
        AnkiConnectClient().verify_note_type()


def test_verify_note_type_ignores_template_ordinal_and_response_order(
    monkeypatch,
) -> None:
    templates = {name: {} for name in ("S", "W", "R", "L")}
    install_valid_note_type(monkeypatch, templates)

    assert AnkiConnectClient().verify_note_type() is True
