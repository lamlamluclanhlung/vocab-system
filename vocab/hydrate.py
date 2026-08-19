"""Fail-closed one-unit T8 audio_1 hydration orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from .anki import AnkiConnectClient
from .contracts import (
    ANKI_NOTE_TYPE_NAME,
    CONTEXT_FIELDS,
    NOTE_FIELDS,
    STATE_FIELDS,
    TARGET_FIELDS,
    TARGET_FLAG_VALUE,
)
from .models import VocabUnit
from .tts import (
    FROZEN_TTS_CONFIG,
    SpeechSynthesizer,
    TtsConfig,
    TtsContractError,
    deterministic_audio_filename,
    parse_audio1_sound_markup,
    sound_markup,
)
from .validators import validate_context_bank, validate_forge_unit


_AUDIO_SNAPSHOT_FIELDS = (
    "unit_key",
    "lemma",
    "lemma_slug",
    "sense_slug",
    "unit_type",
    *TARGET_FIELDS,
    "register",
    "definition_en",
    "source_ref",
    "source_sentence",
    *CONTEXT_FIELDS,
    *STATE_FIELDS,
    "audio_1",
)


class AudioOutcome(str, Enum):
    CREATED = "CREATED"
    ALREADY_READY = "ALREADY_READY"
    SKIPPED_NO_L = "SKIPPED_NO_L"
    STALE = "STALE"


class HydrationError(RuntimeError):
    """Base class for infrastructure, persistence, and safety failures."""


class HydrationNoteError(HydrationError):
    """Raised when notesInfo cannot establish one exact VocabularyUnit note."""


class HydrationCoreInvalidError(HydrationError):
    """Raised when the persisted note fails the Forge-stage precondition."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__(
            "persisted note failed Forge-stage validation: "
            + ", ".join(violations)
        )


class HydrationDependencyError(HydrationError):
    """Raised when empty audio_1 needs an absent synthesizer/config."""


class AudioExistingInvalidError(HydrationError):
    """Raised when persisted audio_1 markup is malformed."""


class AudioMediaMissingOrInvalidError(HydrationError):
    """Raised when referenced or deterministic media is absent or empty."""


class AudioContextNotReadyError(HydrationError):
    """Raised when the note does not have a valid complete context bank."""


class AudioSynthesisError(HydrationError):
    """Raised when a synthesizer returns anything other than non-empty bytes."""


class AudioSynthesisIdentityError(HydrationError):
    """Raised when synthesis identity cannot be bound to frozen D32 config."""


class AudioPersistenceError(HydrationError):
    """Raised when media or audio_1 persistence cannot be proven exact."""


def hydrate_audio(
    note_id: int,
    *,
    anki: AnkiConnectClient,
    synthesizer: SpeechSynthesizer | None = None,
    tts_config: TtsConfig | None = None,
) -> AudioOutcome:
    """Hydrate only audio_1 for one existing, context-ready VocabularyUnit."""
    unit = _load_unit(note_id, anki)
    _require_core_valid(unit)
    if any(value == "" for value in unit.context_fields().values()) or (
        validate_context_bank(unit)
    ):
        raise AudioContextNotReadyError(
            "note must retain a valid complete context bank"
        )

    if unit.Target_L != TARGET_FLAG_VALUE:
        return AudioOutcome.SKIPPED_NO_L

    if unit.audio_1 != "":
        try:
            filename = parse_audio1_sound_markup(unit.audio_1)
        except TtsContractError as exc:
            raise AudioExistingInvalidError(str(exc)) from None
        media = anki.retrieve_media_file(filename)
        if not isinstance(media, bytes) or not media:
            raise AudioMediaMissingOrInvalidError(
                f"referenced media is missing or empty: {filename}"
            )
        return AudioOutcome.ALREADY_READY

    if synthesizer is None:
        raise HydrationDependencyError(
            "empty enabled audio_1 requires a SpeechSynthesizer"
        )
    if tts_config is None:
        raise HydrationDependencyError("empty enabled audio_1 requires a TtsConfig")
    if type(tts_config) is not TtsConfig:
        raise TypeError("tts_config must be a TtsConfig")
    _require_synthesis_identity(synthesizer, tts_config)

    snapshot = _snapshot(unit)
    filename = deterministic_audio_filename(
        config=tts_config,
        unit_key=unit.unit_key,
        text=unit.Ctx_1,
    )
    existing = anki.retrieve_media_file(filename)
    if existing is not None and (
        not isinstance(existing, bytes) or not existing
    ):
        raise AudioMediaMissingOrInvalidError(
            "deterministic media exists but is not non-empty bytes: "
            f"{filename}"
        )

    if existing is None:
        audio = synthesizer.synthesize(text=unit.Ctx_1)
        if not isinstance(audio, bytes) or not audio:
            raise AudioSynthesisError("synthesizer must return non-empty bytes")
        stored_name = anki.store_media_file(filename, audio)
        if stored_name != filename:
            raise AudioPersistenceError(
                "Anki did not preserve the deterministic media filename"
            )
        persisted_audio = anki.retrieve_media_file(filename)
        if persisted_audio != audio:
            raise AudioPersistenceError(
                "stored media bytes did not read back exactly"
            )

    latest = _load_unit(note_id, anki)
    if _snapshot(latest) != snapshot:
        return AudioOutcome.STALE

    markup = sound_markup(filename)
    anki.update_note_fields(note_id, {"audio_1": markup})
    persisted = _load_unit(note_id, anki)
    if persisted.audio_1 != markup:
        raise AudioPersistenceError("audio_1 did not match the exact subset write")
    return AudioOutcome.CREATED


def _require_synthesis_identity(
    synthesizer: SpeechSynthesizer,
    tts_config: TtsConfig,
) -> None:
    try:
        identity = synthesizer.synthesis_identity
    except Exception:
        raise AudioSynthesisIdentityError(
            "synthesizer identity metadata is missing or unreadable"
        ) from None
    if type(identity) is not TtsConfig:
        raise AudioSynthesisIdentityError(
            "synthesizer identity metadata must be a TtsConfig"
        )
    if tts_config != FROZEN_TTS_CONFIG:
        raise AudioSynthesisIdentityError(
            "TtsConfig does not exactly match the frozen D32 configuration"
        )
    if identity != FROZEN_TTS_CONFIG or identity != tts_config:
        raise AudioSynthesisIdentityError(
            "synthesizer identity does not exactly match frozen D32 configuration"
        )


def _load_unit(note_id: int, anki: AnkiConnectClient) -> VocabUnit:
    if type(note_id) is not int:
        raise HydrationNoteError("note_id must be an actual integer")

    notes = anki.notes_info([note_id])
    if not isinstance(notes, list) or len(notes) != 1:
        raise HydrationNoteError(
            "notesInfo must return exactly one note for the requested ID"
        )
    note = notes[0]
    if not isinstance(note, Mapping):
        raise HydrationNoteError("notesInfo note must be an object")
    returned_id = note.get("noteId")
    if type(returned_id) is not int or returned_id != note_id:
        raise HydrationNoteError("notesInfo returned a different note ID")
    if note.get("modelName") != ANKI_NOTE_TYPE_NAME:
        raise HydrationNoteError(
            f"note model must be exactly {ANKI_NOTE_TYPE_NAME!r}"
        )

    raw_fields = note.get("fields")
    if not isinstance(raw_fields, Mapping):
        raise HydrationNoteError("notesInfo fields must be an object")
    if set(raw_fields.keys()) != set(NOTE_FIELDS):
        raise HydrationNoteError(
            "notesInfo field names must match NOTE_FIELDS exactly"
        )

    field_values: dict[str, str] = {}
    for field_name in NOTE_FIELDS:
        record = raw_fields[field_name]
        if not isinstance(record, Mapping) or "value" not in record:
            raise HydrationNoteError(
                f"notesInfo field {field_name!r} must contain a value"
            )
        value = record["value"]
        if not isinstance(value, str):
            raise HydrationNoteError(
                f"notesInfo field {field_name!r} value must be a string"
            )
        field_values[field_name] = value
    return VocabUnit(**field_values)


def _require_core_valid(unit: VocabUnit) -> None:
    violations = validate_forge_unit(unit)
    if violations:
        raise HydrationCoreInvalidError(violations)


def _snapshot(unit: VocabUnit) -> tuple[str, ...]:
    return tuple(getattr(unit, field_name) for field_name in _AUDIO_SNAPSHOT_FIELDS)
