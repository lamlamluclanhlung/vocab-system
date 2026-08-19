"""Fake-only tests for one-unit T8 context and audio hydration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy

import pytest

from vocab.context import ContextPreview
from vocab.contracts import ANKI_NOTE_TYPE_NAME, CONTEXT_FIELDS, NOTE_FIELDS
from vocab.hydrate import (
    AudioExistingInvalidError,
    AudioExistingPartialError,
    AudioMediaMissingOrInvalidError,
    AudioOutcome,
    AudioPersistenceError,
    ContextOutcome,
    ContextPersistenceError,
    HydrationCoreInvalidError,
    HydrationNoteError,
    hydrate_unit,
)
from vocab.models import VocabUnit
from vocab.tts import TtsConfig, deterministic_audio_filename, sound_markup


NOTE_ID = 7001


def valid_contexts() -> dict[str, str]:
    return {
        "Ctx_1": (
            "The subtle change in lighting became obvious after we compared "
            "both photographs carefully."
        ),
        "Ctx_2": (
            "Her subtle humor made the tense committee meeting feel "
            "unexpectedly relaxed today."
        ),
        "Ctx_3": (
            "A subtle flavor of citrus appeared only after the soup had "
            "cooled completely."
        ),
        "Ctx_4": (
            "They noticed a subtle shift in policy during the final "
            "discussion with managers."
        ),
        "Ctx_5": (
            "The artist used a subtle contrast to guide attention toward "
            "the quiet background."
        ),
    }


def make_unit(
    *,
    contexts_ready: bool = True,
    target_l: bool = False,
) -> VocabUnit:
    unit = VocabUnit(
        unit_key="subtle::small-difference",
        lemma="subtle",
        lemma_slug="subtle",
        sense_slug="small-difference",
        unit_type="word",
        Target_R="1",
        Target_L="1" if target_l else "",
        register="neutral",
        definition_en="hard to notice or understand",
        source_ref="dictionary:cambridge:subtle",
        source_sentence=(
            "The subtle distinction between the two proposals escaped most "
            "readers."
        ),
        state_R="NEW",
        state_L="NEW" if target_l else "",
        VisualCue="keep-this-cue",
    )
    if contexts_ready:
        for field_name, value in valid_contexts().items():
            setattr(unit, field_name, value)
    return unit


class FakeAnki:
    def __init__(self, unit: VocabUnit) -> None:
        self.values = unit.to_note_fields()
        self.notes_calls = 0
        self.notes_result_override: object | None = None
        self.notes_hooks: dict[int, Callable[[FakeAnki], None]] = {}
        self.updates: list[tuple[int, dict[str, str]]] = []
        self.apply_updates = True
        self.media: dict[str, bytes] = {}
        self.retrieve_calls: list[str] = []
        self.store_calls: list[tuple[str, bytes]] = []
        self.store_name_override: str | None = None
        self.corrupt_store_readback = False
        self.events: list[tuple[str, str]] = []

    def notes_info(self, note_ids: list[int]) -> object:
        self.notes_calls += 1
        hook = self.notes_hooks.get(self.notes_calls)
        if hook is not None:
            hook(self)
        if self.notes_result_override is not None:
            return deepcopy(self.notes_result_override)
        return [
            {
                "noteId": NOTE_ID,
                "modelName": ANKI_NOTE_TYPE_NAME,
                "fields": {
                    field_name: {"value": self.values[field_name], "order": index}
                    for index, field_name in enumerate(NOTE_FIELDS)
                },
                "tags": ["normal-metadata-is-ignored"],
            }
        ]

    def update_note_fields(
        self,
        note_id: int,
        fields: Mapping[str, str],
    ) -> None:
        copied = dict(fields)
        self.updates.append((note_id, copied))
        self.events.append(("update", ",".join(copied)))
        if self.apply_updates:
            self.values.update(copied)

    def retrieve_media_file(self, filename: str) -> bytes | None:
        self.retrieve_calls.append(filename)
        self.events.append(("retrieve", filename))
        return self.media.get(filename)

    def store_media_file(self, filename: str, data: bytes) -> str:
        self.store_calls.append((filename, data))
        self.events.append(("store", filename))
        self.media[filename] = b"corrupt" if self.corrupt_store_readback else data
        return self.store_name_override or filename


class FakeGenerator:
    def __init__(self, output: Mapping[str, object]) -> None:
        self.output = output
        self.calls: list[tuple[object, Mapping[str, object]]] = []

    def generate(self, request, *, json_schema):
        self.calls.append((request, deepcopy(json_schema)))
        return dict(self.output)


class FakeConfirmation:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.previews: list[ContextPreview] = []

    def __call__(self, preview: ContextPreview) -> bool:
        self.previews.append(preview)
        return self.accepted


class FakeSynthesizer:
    def __init__(self, events: list[tuple[str, str]] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail_on_call: int | None = None
        self.events = events

    def synthesize(self, *, text: str, voice_id: str, locale: str) -> bytes:
        self.calls.append(
            {"text": text, "voice_id": voice_id, "locale": locale}
        )
        if self.events is not None:
            self.events.append(("synthesize", voice_id))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("synthetic provider failure")
        return f"audio:{voice_id}".encode("utf-8")


def tts_config() -> TtsConfig:
    return TtsConfig(
        "southeastasia",
        "en-US",
        ("voice-one", "voice-two", "voice-three"),
    )


def expected_filenames(unit: VocabUnit, config: TtsConfig) -> list[str]:
    return [
        deterministic_audio_filename(
            region=config.region,
            unit_key=unit.unit_key,
            slot=slot,
            text=unit.Ctx_1,
            voice_id=config.voice_ids[slot - 1],
            locale=config.locale,
        )
        for slot in (1, 2, 3)
    ]


def test_loaded_note_requires_actual_integer_id_before_anki_call() -> None:
    anki = FakeAnki(make_unit())

    with pytest.raises(HydrationNoteError, match="actual integer"):
        hydrate_unit(True, anki=anki)

    assert anki.notes_calls == 0


@pytest.mark.parametrize("result", [[], [{"noteId": NOTE_ID}], [{}, {}]])
def test_loaded_note_requires_exactly_one_well_shaped_note(result) -> None:
    anki = FakeAnki(make_unit())
    anki.notes_result_override = result

    with pytest.raises(HydrationNoteError):
        hydrate_unit(NOTE_ID, anki=anki)


def test_loaded_note_rejects_wrong_model() -> None:
    anki = FakeAnki(make_unit())
    note = anki.notes_info([NOTE_ID])[0]
    note["modelName"] = "WrongModel"
    anki.notes_result_override = [note]
    anki.notes_calls = 0

    with pytest.raises(HydrationNoteError, match="model"):
        hydrate_unit(NOTE_ID, anki=anki)


@pytest.mark.parametrize("returned_id", [NOTE_ID + 1, True, "7001"])
def test_loaded_note_rejects_wrong_returned_note_id(returned_id: object) -> None:
    anki = FakeAnki(make_unit())
    note = anki.notes_info([NOTE_ID])[0]
    note["noteId"] = returned_id
    anki.notes_result_override = [note]
    anki.notes_calls = 0

    with pytest.raises(HydrationNoteError, match="different note ID"):
        hydrate_unit(NOTE_ID, anki=anki)


def test_loaded_note_rejects_missing_field() -> None:
    anki = FakeAnki(make_unit())
    note = anki.notes_info([NOTE_ID])[0]
    del note["fields"]["Ctx_5"]
    anki.notes_result_override = [note]
    anki.notes_calls = 0

    with pytest.raises(HydrationNoteError, match="NOTE_FIELDS"):
        hydrate_unit(NOTE_ID, anki=anki)


def test_loaded_note_rejects_malformed_field_value() -> None:
    anki = FakeAnki(make_unit())
    note = anki.notes_info([NOTE_ID])[0]
    note["fields"]["Ctx_5"]["value"] = 5
    anki.notes_result_override = [note]
    anki.notes_calls = 0

    with pytest.raises(HydrationNoteError, match="value must be a string"):
        hydrate_unit(NOTE_ID, anki=anki)


def test_forge_precondition_failure_makes_no_generation_or_write() -> None:
    unit = make_unit(contexts_ready=False)
    unit.definition_en = ""
    anki = FakeAnki(unit)
    generator = FakeGenerator(valid_contexts())

    with pytest.raises(HydrationCoreInvalidError) as captured:
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            generator=generator,
            confirmation=FakeConfirmation(True),
        )

    assert captured.value.violations == ("F_DEFINITION_EMPTY",)
    assert generator.calls == []
    assert anki.updates == []
    assert anki.store_calls == []


def test_empty_context_bank_generates_confirms_and_writes_once() -> None:
    unit = make_unit(contexts_ready=False)
    anki = FakeAnki(unit)
    generator = FakeGenerator(valid_contexts())
    confirmation = FakeConfirmation(True)

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        generator=generator,
        confirmation=confirmation,
    )

    assert result.context_outcome is ContextOutcome.CREATED
    assert result.audio_outcome is AudioOutcome.SKIPPED_NO_L
    assert len(generator.calls) == 1
    request, schema = generator.calls[0]
    assert tuple(request.__dataclass_fields__) == (
        "lemma",
        "unit_type",
        "definition_en",
        "register",
        "source_sentence",
    )
    assert schema["additionalProperties"] is False
    assert len(confirmation.previews) == 1
    assert confirmation.previews[0].validation_passed is True
    assert anki.updates == [(NOTE_ID, valid_contexts())]


def test_generated_validator_rejection_preserves_exact_codes_without_retry() -> None:
    unit = make_unit(contexts_ready=False)
    candidate = valid_contexts()
    candidate["Ctx_1"] = (
        "This ordinary example intentionally omits the required target word "
        "entirely."
    )
    anki = FakeAnki(unit)
    generator = FakeGenerator(candidate)
    confirmation = FakeConfirmation(True)

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        generator=generator,
        confirmation=confirmation,
    )

    assert result.context_outcome is ContextOutcome.GENERATED_INVALID
    assert result.audio_outcome is AudioOutcome.NOT_ATTEMPTED
    assert result.violations == ("C_CTX_1_UNIT_MISSING",)
    assert len(generator.calls) == 1
    assert confirmation.previews == []
    assert anki.updates == []


def test_human_decline_writes_nothing_and_does_no_audio_work() -> None:
    unit = make_unit(contexts_ready=False, target_l=True)
    anki = FakeAnki(unit)
    generator = FakeGenerator(valid_contexts())
    synth = FakeSynthesizer()

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        generator=generator,
        confirmation=FakeConfirmation(False),
        synthesizer=synth,
        tts_config=tts_config(),
    )

    assert result.context_outcome is ContextOutcome.DECLINED
    assert result.audio_outcome is AudioOutcome.NOT_ATTEMPTED
    assert anki.updates == []
    assert synth.calls == []


def test_existing_valid_context_bank_is_not_generated_or_overwritten() -> None:
    anki = FakeAnki(make_unit())
    generator = FakeGenerator(valid_contexts())

    result = hydrate_unit(NOTE_ID, anki=anki, generator=generator)

    assert result.context_outcome is ContextOutcome.ALREADY_READY
    assert result.audio_outcome is AudioOutcome.SKIPPED_NO_L
    assert generator.calls == []
    assert anki.updates == []


def test_existing_partial_context_bank_fails_closed() -> None:
    unit = make_unit(contexts_ready=False)
    unit.Ctx_1 = valid_contexts()["Ctx_1"]
    anki = FakeAnki(unit)
    generator = FakeGenerator(valid_contexts())

    result = hydrate_unit(NOTE_ID, anki=anki, generator=generator)

    assert result.context_outcome is ContextOutcome.EXISTING_PARTIAL
    assert generator.calls == []
    assert anki.updates == []


def test_existing_full_invalid_context_preserves_validator_codes() -> None:
    unit = make_unit()
    unit.Ctx_4 = "Whitespace-free but missing the required lexical item here."
    anki = FakeAnki(unit)

    result = hydrate_unit(NOTE_ID, anki=anki)

    assert result.context_outcome is ContextOutcome.EXISTING_INVALID
    assert result.violations == ("C_CTX_4_UNIT_MISSING",)
    assert anki.updates == []


def test_whitespace_contexts_are_not_normalized_to_empty_eligibility() -> None:
    unit = make_unit(contexts_ready=False)
    for field_name in CONTEXT_FIELDS:
        setattr(unit, field_name, "   ")
    anki = FakeAnki(unit)
    generator = FakeGenerator(valid_contexts())

    result = hydrate_unit(NOTE_ID, anki=anki, generator=generator)

    assert result.context_outcome is ContextOutcome.EXISTING_INVALID
    assert result.violations == tuple(
        f"C_CTX_{index}_EMPTY" for index in range(1, 6)
    )
    assert generator.calls == []


def test_context_stale_snapshot_after_confirmation_writes_nothing() -> None:
    unit = make_unit(contexts_ready=False)
    anki = FakeAnki(unit)
    anki.notes_hooks[2] = lambda fake: fake.values.update(
        {"definition_en": "human edited definition"}
    )

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        generator=FakeGenerator(valid_contexts()),
        confirmation=FakeConfirmation(True),
    )

    assert result.context_outcome is ContextOutcome.STALE
    assert result.audio_outcome is AudioOutcome.NOT_ATTEMPTED
    assert anki.updates == []


def test_context_readback_mismatch_is_persistence_failure() -> None:
    anki = FakeAnki(make_unit(contexts_ready=False))
    anki.apply_updates = False

    with pytest.raises(ContextPersistenceError):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            generator=FakeGenerator(valid_contexts()),
            confirmation=FakeConfirmation(True),
        )


def test_hydration_never_uses_full_replacement_serialization(monkeypatch) -> None:
    anki = FakeAnki(make_unit(contexts_ready=False))

    def forbidden_to_note_fields(_self):
        raise AssertionError("T8 must not call to_note_fields")

    monkeypatch.setattr(VocabUnit, "to_note_fields", forbidden_to_note_fields)

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        generator=FakeGenerator(valid_contexts()),
        confirmation=FakeConfirmation(True),
    )

    assert result.context_outcome is ContextOutcome.CREATED


def test_target_l_disabled_does_not_touch_existing_audio() -> None:
    unit = make_unit()
    unit.audio_1 = "legacy one"
    unit.audio_2 = "legacy two"
    anki = FakeAnki(unit)
    synth = FakeSynthesizer()

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=tts_config(),
    )

    assert result.audio_outcome is AudioOutcome.SKIPPED_NO_L
    assert synth.calls == []
    assert anki.retrieve_calls == []
    assert anki.store_calls == []
    assert anki.updates == []
    assert anki.values["audio_1"] == "legacy one"


def test_complete_valid_audio_and_media_is_already_ready_despite_config_drift() -> None:
    unit = make_unit(target_l=True)
    filenames = [
        f"vocab-a{slot}-{str(slot) * 16}.mp3"
        for slot in (1, 2, 3)
    ]
    for slot, filename in enumerate(filenames, start=1):
        setattr(unit, f"audio_{slot}", sound_markup(filename))
    anki = FakeAnki(unit)
    anki.media = {filename: f"old-{filename}".encode() for filename in filenames}
    synth = FakeSynthesizer()
    drifted = TtsConfig("changedregion", "en-GB", ("new-1", "new-2", "new-3"))

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=drifted,
    )

    assert result.audio_outcome is AudioOutcome.ALREADY_READY
    assert anki.retrieve_calls == filenames
    assert synth.calls == []
    assert anki.store_calls == []
    assert anki.updates == []


def test_fully_hydrated_enabled_note_needs_no_provider_dependencies() -> None:
    unit = make_unit(target_l=True)
    filenames = [
        f"vocab-a{slot}-{str(slot) * 16}.mp3"
        for slot in (1, 2, 3)
    ]
    for slot, filename in enumerate(filenames, start=1):
        setattr(unit, f"audio_{slot}", sound_markup(filename))
    anki = FakeAnki(unit)
    anki.media = {filename: b"accepted" for filename in filenames}

    result = hydrate_unit(NOTE_ID, anki=anki)

    assert result.context_outcome is ContextOutcome.ALREADY_READY
    assert result.audio_outcome is AudioOutcome.ALREADY_READY


def test_partial_audio_fails_before_synthesis_or_write() -> None:
    unit = make_unit(target_l=True)
    unit.audio_1 = "[sound:vocab-a1-0000000000000000.mp3]"
    anki = FakeAnki(unit)
    synth = FakeSynthesizer()

    with pytest.raises(AudioExistingPartialError):
        hydrate_unit(NOTE_ID, anki=anki, synthesizer=synth, tts_config=tts_config())

    assert synth.calls == []
    assert anki.updates == []


def test_complete_malformed_audio_fails_closed() -> None:
    unit = make_unit(target_l=True)
    unit.audio_1 = "arbitrary.mp3"
    unit.audio_2 = "arbitrary.mp3"
    unit.audio_3 = "arbitrary.mp3"
    anki = FakeAnki(unit)

    with pytest.raises(AudioExistingInvalidError):
        hydrate_unit(NOTE_ID, anki=anki)


@pytest.mark.parametrize("media_value", [None, b"", "not-bytes"])
def test_complete_audio_with_missing_or_empty_media_fails_without_repair(
    media_value: object,
) -> None:
    unit = make_unit(target_l=True)
    filenames = [
        f"vocab-a{slot}-{str(slot) * 16}.mp3"
        for slot in (1, 2, 3)
    ]
    for slot, filename in enumerate(filenames, start=1):
        setattr(unit, f"audio_{slot}", sound_markup(filename))
    anki = FakeAnki(unit)
    anki.media = {filename: b"valid" for filename in filenames}
    if media_value is None:
        del anki.media[filenames[1]]
    else:
        anki.media[filenames[1]] = media_value  # type: ignore[assignment]

    with pytest.raises(AudioMediaMissingOrInvalidError):
        hydrate_unit(NOTE_ID, anki=anki)

    assert anki.store_calls == []
    assert anki.updates == []


def test_empty_audio_reuses_orphans_synthesizes_missing_and_commits_atomically() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    config = tts_config()
    filenames = expected_filenames(unit, config)
    anki.media[filenames[0]] = b"existing-orphan"
    synth = FakeSynthesizer(anki.events)

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=config,
    )

    assert result.audio_outcome is AudioOutcome.CREATED
    assert [call["text"] for call in synth.calls] == [unit.Ctx_1, unit.Ctx_1]
    assert [call["voice_id"] for call in synth.calls] == ["voice-two", "voice-three"]
    assert [call["locale"] for call in synth.calls] == ["en-US", "en-US"]
    assert anki.store_calls == [
        (filenames[1], b"audio:voice-two"),
        (filenames[2], b"audio:voice-three"),
    ]
    assert anki.updates == [
        (
            NOTE_ID,
            {
                "audio_1": sound_markup(filenames[0]),
                "audio_2": sound_markup(filenames[1]),
                "audio_3": sound_markup(filenames[2]),
            },
        )
    ]
    assert anki.values["VisualCue"] == "keep-this-cue"
    assert anki.events.index(("retrieve", filenames[1])) < anki.events.index(
        ("synthesize", "voice-two")
    )
    assert all(event[0] != "update" for event in anki.events[:-1])


def test_all_three_slots_synthesize_exact_same_ctx_1_with_ordered_voices() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    config = tts_config()
    synth = FakeSynthesizer()

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=config,
    )

    assert result.audio_outcome is AudioOutcome.CREATED
    assert [call["text"] for call in synth.calls] == [unit.Ctx_1] * 3
    assert [call["voice_id"] for call in synth.calls] == list(config.voice_ids)
    assert [call["locale"] for call in synth.calls] == [config.locale] * 3
    assert [filename for filename, _data in anki.store_calls] == (
        expected_filenames(unit, config)
    )


def test_empty_deterministic_orphan_fails_without_synthesis_or_write() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    config = tts_config()
    filename = expected_filenames(unit, config)[0]
    anki.media[filename] = b""
    synth = FakeSynthesizer()

    with pytest.raises(AudioMediaMissingOrInvalidError, match="non-empty bytes"):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=config,
        )

    assert synth.calls == []
    assert anki.updates == []


def test_store_returned_filename_mismatch_fails_without_audio_field_write() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    anki.store_name_override = "renamed.mp3"

    with pytest.raises(AudioPersistenceError, match="filename"):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=tts_config(),
        )

    assert anki.updates == []


def test_stored_media_byte_mismatch_fails_without_audio_field_write() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    anki.corrupt_store_readback = True

    with pytest.raises(AudioPersistenceError, match="bytes"):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=tts_config(),
        )

    assert anki.updates == []


def test_slot_two_synthesis_failure_leaves_orphan_without_field_write() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    config = tts_config()
    filenames = expected_filenames(unit, config)
    synth = FakeSynthesizer()
    synth.fail_on_call = 2

    with pytest.raises(RuntimeError, match="provider failure"):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=config,
        )

    assert anki.media[filenames[0]] == b"audio:voice-one"
    assert filenames[1] not in anki.media
    assert anki.updates == []


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("Target_L", ""),
        ("Ctx_1", "A human changed this subtle context before commit."),
        ("audio_2", "[sound:vocab-a2-ffffffffffffffff.mp3]"),
    ],
)
def test_audio_stale_guard_prevents_field_commit(
    field_name: str,
    replacement: str,
) -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    anki.notes_hooks[3] = lambda fake: fake.values.update(
        {field_name: replacement}
    )

    result = hydrate_unit(
        NOTE_ID,
        anki=anki,
        synthesizer=FakeSynthesizer(),
        tts_config=tts_config(),
    )

    assert result.audio_outcome is AudioOutcome.STALE
    assert anki.updates == []
    assert anki.store_calls
    assert anki.values["VisualCue"] == "keep-this-cue"


def test_audio_field_readback_mismatch_is_persistence_failure() -> None:
    unit = make_unit(target_l=True)
    anki = FakeAnki(unit)
    anki.apply_updates = False

    with pytest.raises(AudioPersistenceError, match="fields"):
        hydrate_unit(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=tts_config(),
        )

    assert len(anki.updates) == 1
    assert set(anki.updates[0][1]) == {"audio_1", "audio_2", "audio_3"}
    assert anki.values["VisualCue"] == "keep-this-cue"
