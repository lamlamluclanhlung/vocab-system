"""Small, fail-closed AnkiConnect client for vocabulary persistence."""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import math
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from .anki_template import AnkiTemplateViolation, verify_model_snapshot
from .contracts import (
    ANKI_NOTE_TYPE_NAME,
    IMMUTABLE_NOTE_FIELDS,
    NOTE_FIELDS,
)
from .leech import (
    LeechConfigViolation,
    verify_leech_config as verify_leech_config_snapshot,
)

from .models import VocabUnit


ANKI_CONNECT_VERSION = 6
DEFAULT_ANKI_CONNECT_ENDPOINT = "http://127.0.0.1:8765"
DEFAULT_ANKI_CONNECT_TIMEOUT = 10.0


class AnkiConnectError(RuntimeError):
    """Base class for failures reported by this AnkiConnect client."""


class AnkiConnectionError(AnkiConnectError):
    """Raised when the AnkiConnect HTTP request cannot be completed."""

    def __init__(self, action: str, endpoint: str, cause: BaseException) -> None:
        self.action = action
        self.endpoint = endpoint
        self.cause = cause
        super().__init__(
            f"AnkiConnect action {action!r} could not reach {endpoint}: {cause}"
        )


class AnkiAPIError(AnkiConnectError):
    """Raised when AnkiConnect returns a non-null error value."""

    def __init__(self, action: str, error: object, result: object) -> None:
        self.action = action
        self.error = error
        self.result = result
        super().__init__(f"AnkiConnect action {action!r} failed: {error}")


class AnkiResponseError(AnkiConnectError):
    """Raised when an AnkiConnect response cannot be trusted or interpreted."""

    def __init__(
        self,
        action: str,
        message: str,
        *,
        response: object | None = None,
    ) -> None:
        self.action = action
        self.response = response
        super().__init__(f"invalid response for AnkiConnect action {action!r}: {message}")


class AnkiNoteCreationError(AnkiConnectError):
    """Raised when addNotes reports failed or potentially partial creation."""

    def __init__(self, expected_count: int, result: object) -> None:
        self.expected_count = expected_count
        self.result = result
        if isinstance(result, list):
            self.failed_indexes = tuple(
                index
                for index in range(expected_count)
                if index >= len(result) or type(result[index]) is not int
            )
        else:
            self.failed_indexes = tuple(range(expected_count))
        super().__init__(
            "addNotes did not confirm every requested note creation; "
            f"expected {expected_count} note IDs, received {result!r}"
        )


class AnkiNoteTypeMismatchError(AnkiConnectError):
    """Raised when the installed VocabularyUnit note type violates contracts."""


class AnkiCardTemplateError(AnkiNoteTypeMismatchError):
    """Raised with all deterministic note-type semantic violations."""

    def __init__(
        self,
        violations: Sequence[AnkiTemplateViolation],
    ) -> None:
        self.violations = tuple(violations)
        super().__init__(
            "Anki note type semantic verification failed: "
            + "; ".join(str(violation) for violation in self.violations)
        )


class AnkiLeechConfigMismatchError(AnkiConnectError):
    """Raised with all deterministic leech-configuration violations."""

    def __init__(
        self,
        violations: Sequence[LeechConfigViolation],
    ) -> None:
        self.violations = tuple(violations)
        super().__init__(
            "Anki leech configuration verification failed: "
            + "; ".join(
                f"{violation.code}: {violation.message}"
                for violation in self.violations
            )
        )


class AnkiConnectClient:
    """Direct AnkiConnect version 6 client with one HTTP attempt per action."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ANKI_CONNECT_ENDPOINT,
        timeout: float = DEFAULT_ANKI_CONNECT_TIMEOUT,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint must be a non-empty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a finite positive number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        self.endpoint = endpoint
        self.timeout = float(timeout)

    def _invoke(self, action: str, params: Mapping[str, Any]) -> Any:
        envelope = {
            "action": action,
            "version": ANKI_CONNECT_VERSION,
            "params": dict(params),
        }
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_response = response.read()
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            raise AnkiConnectionError(action, self.endpoint, exc) from exc

        if not isinstance(raw_response, bytes):
            raise AnkiResponseError(
                action,
                "HTTP body was not bytes",
                response=raw_response,
            )
        try:
            decoded = raw_response.decode("utf-8")
            response_envelope = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnkiResponseError(
                action,
                f"body is not valid UTF-8 JSON: {exc}",
                response=raw_response,
            ) from exc

        if not isinstance(response_envelope, dict):
            raise AnkiResponseError(
                action,
                "response envelope must be a JSON object",
                response=response_envelope,
            )
        missing = {"result", "error"}.difference(response_envelope)
        if missing:
            raise AnkiResponseError(
                action,
                f"response envelope is missing {tuple(sorted(missing))}",
                response=response_envelope,
            )
        if response_envelope["error"] is not None:
            raise AnkiAPIError(
                action,
                response_envelope["error"],
                response_envelope["result"],
            )
        return response_envelope["result"]

    def add_notes(self, deck_name: str, units: Sequence[VocabUnit]) -> list[int]:
        """Create complete VocabularyUnit notes in the runtime-selected deck."""
        if not isinstance(deck_name, str) or not deck_name:
            raise ValueError("deck_name must be a non-empty string")

        notes: list[dict[str, Any]] = []
        for index, unit in enumerate(units):
            if not isinstance(unit, VocabUnit):
                raise TypeError(f"units[{index}] must be a VocabUnit")
            notes.append(
                {
                    "deckName": deck_name,
                    "modelName": ANKI_NOTE_TYPE_NAME,
                    "fields": unit.to_note_fields(),
                    "options": {"allowDuplicate": False},
                    "tags": [],
                }
            )

        result = self._invoke("addNotes", {"notes": notes})
        if (
            not isinstance(result, list)
            or len(result) != len(notes)
            or any(type(note_id) is not int for note_id in result)
        ):
            raise AnkiNoteCreationError(len(notes), result)
        return result

    def find_notes(self, query: str) -> list[int]:
        """Return note IDs matching an Anki search query."""
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        result = self._invoke("findNotes", {"query": query})
        return self._require_id_list("findNotes", result)

    def notes_info(self, note_ids: Sequence[int]) -> list[dict[str, Any]]:
        """Return Anki note information for explicit note IDs."""
        notes = self._normalize_ids("note_ids", note_ids)
        result = self._invoke("notesInfo", {"notes": notes})
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise AnkiResponseError(
                "notesInfo",
                "result must be a list of note objects",
                response=result,
            )
        return result

    def cards_info(self, card_ids: Sequence[int]) -> list[dict[str, Any]]:
        """Return current Anki card information for explicit card IDs."""
        cards = self._normalize_ids("card_ids", card_ids)
        result = self._invoke("cardsInfo", {"cards": cards})
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise AnkiResponseError(
                "cardsInfo",
                "result must be a list of card objects",
                response=result,
            )
        return result

    def update_note_fields(
        self,
        note_id: int,
        fields: Mapping[str, str],
    ) -> None:
        """Update only the explicitly supplied subset of VocabularyUnit fields."""
        self._require_id("note_id", note_id)
        if not isinstance(fields, Mapping):
            raise TypeError("fields must be a mapping")
        if not fields:
            raise ValueError("fields must contain at least one explicit update")

        unknown_fields = set(fields).difference(NOTE_FIELDS)
        if unknown_fields:
            raise ValueError(
                f"unknown VocabularyUnit fields: {tuple(sorted(unknown_fields))}"
            )
        
        immutable_fields = set(fields).intersection(IMMUTABLE_NOTE_FIELDS)
        if immutable_fields:
            raise ValueError(
                "immutable VocabularyUnit fields cannot be updated: "
                f"{tuple(sorted(immutable_fields))}"
            )
        
        if any(not isinstance(value, str) for value in fields.values()):
            raise TypeError("field values must be strings")

        result = self._invoke(
            "updateNoteFields",
            {"note": {"id": note_id, "fields": dict(fields)}},
        )
        self._require_null_result("updateNoteFields", result)

    def suspend(self, card_ids: Sequence[int]) -> bool:
        """Suspend cards by card ID, preserving their notes and revlog."""
        cards = self._normalize_ids("card_ids", card_ids)
        return self._require_bool_result(
            "suspend",
            self._invoke("suspend", {"cards": cards}),
        )

    def unsuspend(self, card_ids: Sequence[int]) -> None:
        """Unsuspend cards by card ID."""
        cards = self._normalize_ids("card_ids", card_ids)
        result = self._invoke("unsuspend", {"cards": cards})
        self._require_null_result("unsuspend", result)

    def get_revlog(
        self,
        card_ids: Sequence[int],
    ) -> dict[str, list[dict[str, Any]]]:
        """Read review history directly from Anki's revlog for card IDs."""
        cards = self._normalize_ids("card_ids", card_ids)
        result = self._invoke("getReviewsOfCards", {"cards": cards})
        if not isinstance(result, dict) or any(
            not isinstance(card_id, str)
            or not isinstance(reviews, list)
            or any(not isinstance(review, dict) for review in reviews)
            for card_id, reviews in result.items()
        ):
            raise AnkiResponseError(
                "getReviewsOfCards",
                "result must map card ID strings to lists of review objects",
                response=result,
            )
        return result

    def store_media_file(self, filename: str, data: bytes) -> str:
        """Store raw bytes through AnkiConnect and return Anki's actual filename."""
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty string")
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        encoded = base64.b64encode(data).decode("ascii")
        result = self._invoke(
            "storeMediaFile",
            {
                "filename": filename,
                "data": encoded,
                "deleteExisting": False,
            },
        )
        if not isinstance(result, str) or not result:
            raise AnkiResponseError(
                "storeMediaFile",
                "result must be the non-empty filename stored by Anki",
                response=result,
            )
        return result

    def retrieve_media_file(self, filename: str) -> bytes | None:
        """Retrieve exact media bytes, or None when Anki reports no file."""
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty string")

        result = self._invoke(
            "retrieveMediaFile",
            {"filename": filename},
        )
        if result is False:
            return None
        if not isinstance(result, str):
            raise AnkiResponseError(
                "retrieveMediaFile",
                "result must be false or a base64 string",
                response=result,
            )
        try:
            return base64.b64decode(result, validate=True)
        except (binascii.Error, ValueError):
            raise AnkiResponseError(
                "retrieveMediaFile",
                "result must be strict base64",
                response=result,
            ) from None

    def get_deck_config(self, deck_name: str) -> dict[str, Any]:
        """Return one caller-selected deck's Anki option configuration."""
        if not isinstance(deck_name, str) or not deck_name:
            raise ValueError("deck_name must be a non-empty string")

        result = self._invoke("getDeckConfig", {"deck": deck_name})
        if not isinstance(result, Mapping):
            raise AnkiResponseError(
                "getDeckConfig",
                "result must be a deck configuration object",
                response=result,
            )
        return dict(result)

    def verify_leech_config(self, deck_name: str) -> bool:
        """Read and verify one deck's leech options without mutation."""
        config = self.get_deck_config(deck_name)
        violations = verify_leech_config_snapshot(config)
        if violations:
            raise AnkiLeechConfigMismatchError(violations)
        return True

    def verified_note_type_snapshot(self) -> dict[str, Any]:
        """Return the complete verified VocabularyUnit model snapshot."""
        models = self._invoke(
            "findModelsByName",
            {"modelNames": [ANKI_NOTE_TYPE_NAME]},
        )
        if not isinstance(models, list) or any(
            not isinstance(model, dict) for model in models
        ):
            raise AnkiResponseError(
                "findModelsByName",
                "result must be a list of model objects",
                response=models,
            )
        if len(models) != 1:
            raise AnkiNoteTypeMismatchError(
                f"findModelsByName must return exactly one "
                f"{ANKI_NOTE_TYPE_NAME!r} model, received {len(models)}"
            )

        violations = verify_model_snapshot(models[0])
        if violations:
            raise AnkiCardTemplateError(violations)
        return models[0]

    def verify_note_type(self) -> bool:
        """Read and verify the complete note-type snapshot without repair."""
        self.verified_note_type_snapshot()
        return True

    @staticmethod
    def _require_id(name: str, value: int) -> None:
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")

    @classmethod
    def _normalize_ids(cls, name: str, values: Sequence[int]) -> list[int]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise TypeError(f"{name} must be a sequence of integers")
        normalized = list(values)
        for index, value in enumerate(normalized):
            cls._require_id(f"{name}[{index}]", value)
        return normalized

    @staticmethod
    def _require_id_list(action: str, result: object) -> list[int]:
        if not isinstance(result, list) or any(
            type(item) is not int for item in result
        ):
            raise AnkiResponseError(
                action,
                "result must be a list of integer IDs",
                response=result,
            )
        return result

    @staticmethod
    def _require_null_result(action: str, result: object) -> None:
        if result is not None:
            raise AnkiResponseError(
                action,
                "result must be null",
                response=result,
            )

    @staticmethod
    def _require_bool_result(action: str, result: object) -> bool:
        if not isinstance(result, bool):
            raise AnkiResponseError(
                action,
                "result must be a boolean",
                response=result,
            )
        return result
