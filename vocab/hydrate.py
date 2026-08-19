"""Fail-closed one-unit T8 context and audio hydration orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .anki import AnkiConnectClient
from .context import (
    ContextGenerationRequest,
    ContextGenerator,
    ContextPreview,
    context_json_schema,
    parse_context_bank,
)
from .contracts import (
    ANKI_NOTE_TYPE_NAME,
    CONTEXT_FIELDS,
    NOTE_FIELDS,
    TARGET_FLAG_VALUE,
)
from .media_contract import AUDIO_SLOT_FIELDS, AUDIO_SLOT_NUMBERS
from .models import VocabUnit
from .tts import (
    SpeechSynthesizer,
    TtsConfig,
    TtsContractError,
    deterministic_audio_filename,
    parse_t8_sound_markup,
    sound_markup,
)
from .validators import validate_context_bank, validate_forge_unit


_CONTEXT_SNAPSHOT_FIELDS = (
    "unit_key",
    "lemma",
    "lemma_slug",
    "sense_slug",
    "unit_type",
    "definition_en",
    "register",
    "source_ref",
    "source_sentence",
    *CONTEXT_FIELDS,
)

_AUDIO_SNAPSHOT_FIELDS = (
    "unit_key",
    "Target_L",
    "Ctx_1",
    *AUDIO_SLOT_FIELDS,
)


class ContextOutcome(str, Enum):
    CREATED = "CREATED"
    ALREADY_READY = "ALREADY_READY"
    DECLINED = "DECLINED"
    GENERATED_INVALID = "GENERATED_INVALID"
    EXISTING_PARTIAL = "EXISTING_PARTIAL"
    EXISTING_INVALID = "EXISTING_INVALID"
    STALE = "STALE"


class AudioOutcome(str, Enum):
    CREATED = "CREATED"
    ALREADY_READY = "ALREADY_READY"
    SKIPPED_NO_L = "SKIPPED_NO_L"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class HydrationResult:
    """Deterministic outcomes for the two sequential T8 artifact stages."""

    context_outcome: ContextOutcome
    audio_outcome: AudioOutcome
    violations: tuple[str, ...] = ()


class ContextConfirmation(Protocol):
    """Human confirmation boundary for an immutable validated preview."""

    def __call__(self, preview: ContextPreview) -> bool: ...


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
    """Raised when the current persisted stage needs an absent dependency."""


class HydrationConfirmationError(HydrationError):
    """Raised when the confirmation boundary does not return an actual bool."""


class ContextPersistenceError(HydrationError):
    """Raised when the atomic context subset write cannot be read back exactly."""


class AudioExistingPartialError(HydrationError):
    """Raised when only part of the persisted three-field audio set exists."""


class AudioExistingInvalidError(HydrationError):
    """Raised when a complete audio set has malformed or wrong-slot markup."""


class AudioMediaMissingOrInvalidError(HydrationError):
    """Raised when referenced or deterministic media is absent or empty."""


class AudioContextNotReadyError(HydrationError):
    """Raised when the latest note no longer has a valid complete context bank."""


class AudioSynthesisError(HydrationError):
    """Raised when a synthesizer returns anything other than non-empty bytes."""


class AudioPersistenceError(HydrationError):
    """Raised when deterministic media or audio field persistence is uncertain."""


def hydrate_unit(
    note_id: int,
    *,
    anki: AnkiConnectClient,
    generator: ContextGenerator | None = None,
    confirmation: ContextConfirmation | None = None,
    synthesizer: SpeechSynthesizer | None = None,
    tts_config: TtsConfig | None = None,
) -> HydrationResult:
    """Hydrate one existing VocabularyUnit without repair or regeneration."""
    unit = _load_unit(note_id, anki)
    _require_core_valid(unit)

    context_values = unit.context_fields()
    context_empty = tuple(value == "" for value in context_values.values())
    if all(context_empty):
        if generator is None:
            raise HydrationDependencyError(
                "context generation requires a ContextGenerator"
            )
        if confirmation is None:
            raise HydrationDependencyError(
                "context generation requires human confirmation"
            )
        context_result, context_violations = _create_context_bank(
            note_id,
            unit,
            anki=anki,
            generator=generator,
            confirmation=confirmation,
        )
        if context_result is not ContextOutcome.CREATED:
            return HydrationResult(
                context_result,
                AudioOutcome.NOT_ATTEMPTED,
                context_violations,
            )
        context_outcome = ContextOutcome.CREATED
    elif all(not is_empty for is_empty in context_empty):
        violations = validate_context_bank(unit)
        if violations:
            return HydrationResult(
                ContextOutcome.EXISTING_INVALID,
                AudioOutcome.NOT_ATTEMPTED,
                violations,
            )
        context_outcome = ContextOutcome.ALREADY_READY
    else:
        return HydrationResult(
            ContextOutcome.EXISTING_PARTIAL,
            AudioOutcome.NOT_ATTEMPTED,
        )

    audio_outcome = _hydrate_audio(
        note_id,
        anki=anki,
        synthesizer=synthesizer,
        tts_config=tts_config,
    )
    return HydrationResult(context_outcome, audio_outcome)


def _create_context_bank(
    note_id: int,
    unit: VocabUnit,
    *,
    anki: AnkiConnectClient,
    generator: ContextGenerator,
    confirmation: ContextConfirmation,
) -> tuple[ContextOutcome, tuple[str, ...]]:
    snapshot = _snapshot(unit, _CONTEXT_SNAPSHOT_FIELDS)
    request = ContextGenerationRequest(
        lemma=unit.lemma,
        unit_type=unit.unit_type,
        definition_en=unit.definition_en,
        register=unit.register,
        source_sentence=unit.source_sentence,
    )
    generated = generator.generate(
        request,
        json_schema=context_json_schema(),
    )
    context_fields = parse_context_bank(generated)
    candidate = replace(unit, **context_fields)
    violations = validate_context_bank(candidate)
    if violations:
        return ContextOutcome.GENERATED_INVALID, violations

    preview = ContextPreview(
        unit_key=candidate.unit_key,
        lemma=candidate.lemma,
        definition_en=candidate.definition_en,
        register=candidate.register,
        **context_fields,
    )
    confirmed = confirmation(preview)
    if type(confirmed) is not bool:
        raise HydrationConfirmationError(
            "confirmation must return an actual bool"
        )
    if not confirmed:
        return ContextOutcome.DECLINED, ()

    latest = _load_unit(note_id, anki)
    if _snapshot(latest, _CONTEXT_SNAPSHOT_FIELDS) != snapshot:
        return ContextOutcome.STALE, ()

    anki.update_note_fields(note_id, context_fields)
    persisted = _load_unit(note_id, anki)
    if persisted.context_fields() != context_fields:
        raise ContextPersistenceError(
            "context fields did not match the confirmed atomic write"
        )
    return ContextOutcome.CREATED, ()


def _hydrate_audio(
    note_id: int,
    *,
    anki: AnkiConnectClient,
    synthesizer: SpeechSynthesizer | None,
    tts_config: TtsConfig | None,
) -> AudioOutcome:
    unit = _load_unit(note_id, anki)
    _require_core_valid(unit)
    if any(value == "" for value in unit.context_fields().values()) or (
        validate_context_bank(unit)
    ):
        raise AudioContextNotReadyError(
            "latest note must retain a valid complete context bank"
        )

    if unit.Target_L != TARGET_FLAG_VALUE:
        return AudioOutcome.SKIPPED_NO_L

    audio_values = unit.audio_fields()
    audio_empty = tuple(value == "" for value in audio_values.values())
    if all(not is_empty for is_empty in audio_empty):
        filenames: list[str] = []
        try:
            for field_name in AUDIO_SLOT_FIELDS:
                filenames.append(
                    parse_t8_sound_markup(field_name, audio_values[field_name])
                )
        except TtsContractError as exc:
            raise AudioExistingInvalidError(str(exc)) from None

        for filename in filenames:
            media = anki.retrieve_media_file(filename)
            if not isinstance(media, bytes) or not media:
                raise AudioMediaMissingOrInvalidError(
                    f"referenced media is missing or empty: {filename}"
                )
        return AudioOutcome.ALREADY_READY

    if not all(audio_empty):
        raise AudioExistingPartialError(
            "audio_1 through audio_3 must be all empty or all populated"
        )

    if synthesizer is None:
        raise HydrationDependencyError(
            "empty enabled audio requires a SpeechSynthesizer"
        )
    if tts_config is None:
        raise HydrationDependencyError(
            "empty enabled audio requires a TtsConfig"
        )
    if not isinstance(tts_config, TtsConfig):
        raise TypeError("tts_config must be a TtsConfig")

    snapshot = _snapshot(unit, _AUDIO_SNAPSHOT_FIELDS)
    filenames: list[str] = []
    for slot in AUDIO_SLOT_NUMBERS:
        voice_id = tts_config.voice_ids[slot - 1]
        filename = deterministic_audio_filename(
            region=tts_config.region,
            unit_key=unit.unit_key,
            slot=slot,
            text=unit.Ctx_1,
            voice_id=voice_id,
            locale=tts_config.locale,
        )
        filenames.append(filename)
        existing = anki.retrieve_media_file(filename)
        if existing is not None and (
            not isinstance(existing, bytes) or not existing
        ):
            raise AudioMediaMissingOrInvalidError(
                "deterministic media exists but is not non-empty bytes: "
                f"{filename}"
            )
        if existing is not None:
            continue

        audio = synthesizer.synthesize(
            text=unit.Ctx_1,
            voice_id=voice_id,
            locale=tts_config.locale,
        )
        if not isinstance(audio, bytes) or not audio:
            raise AudioSynthesisError(
                "synthesizer must return non-empty bytes"
            )
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
    if _snapshot(latest, _AUDIO_SNAPSHOT_FIELDS) != snapshot:
        return AudioOutcome.STALE

    audio_fields = {
        field_name: sound_markup(filename)
        for field_name, filename in zip(AUDIO_SLOT_FIELDS, filenames)
    }
    anki.update_note_fields(note_id, audio_fields)
    persisted = _load_unit(note_id, anki)
    if persisted.audio_fields() != audio_fields:
        raise AudioPersistenceError(
            "audio fields did not match the atomic subset write"
        )
    return AudioOutcome.CREATED


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


def _snapshot(
    unit: VocabUnit,
    field_names: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(getattr(unit, field_name) for field_name in field_names)
