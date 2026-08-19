"""Fake-only tests for one-unit T8.1 audio_1 hydration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace

import pytest

from vocab.contracts import ANKI_NOTE_TYPE_NAME, NOTE_FIELDS
from vocab.hydrate import (
    AudioContextNotReadyError,
    AudioExistingInvalidError,
    AudioMediaMissingOrInvalidError,
    AudioOutcome,
    AudioPersistenceError,
    AudioSynthesisIdentityError,
    HydrationCoreInvalidError,
    HydrationNoteError,
    hydrate_audio,
)
from vocab.models import VocabUnit
from vocab.tts import (
    FROZEN_TTS_CONFIG,
    TtsConfig,
    deterministic_audio_filename,
    sound_markup,
)


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


def make_unit(*, target_l: bool = True, contexts_ready: bool = True) -> VocabUnit:
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
        self.media: dict[str, object] = {}
        self.retrieve_calls: list[str] = []
        self.store_calls: list[tuple[str, bytes]] = []
        self.store_name_override: str | None = None
        self.corrupt_store_readback = False

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
            }
        ]

    def update_note_fields(
        self,
        note_id: int,
        fields: Mapping[str, str],
    ) -> None:
        copied = dict(fields)
        self.updates.append((note_id, copied))
        if self.apply_updates:
            self.values.update(copied)

    def retrieve_media_file(self, filename: str) -> object:
        self.retrieve_calls.append(filename)
        return self.media.get(filename)

    def store_media_file(self, filename: str, data: bytes) -> str:
        self.store_calls.append((filename, data))
        self.media[filename] = b"corrupt" if self.corrupt_store_readback else data
        return self.store_name_override or filename


class FakeSynthesizer:
    def __init__(
        self,
        identity: object = FROZEN_TTS_CONFIG,
        audio: object = b"local-mp3",
    ) -> None:
        self._identity = identity
        self.audio = audio
        self.identity_reads = 0
        self.calls: list[str] = []

    @property
    def synthesis_identity(self) -> object:
        self.identity_reads += 1
        return self._identity

    def synthesize(self, *, text: str) -> object:
        self.calls.append(text)
        return self.audio


def expected_filename(unit: VocabUnit) -> str:
    return deterministic_audio_filename(
        config=FROZEN_TTS_CONFIG,
        unit_key=unit.unit_key,
        text=unit.Ctx_1,
    )


def test_loaded_note_requires_actual_integer_id_before_anki_call() -> None:
    anki = FakeAnki(make_unit())

    with pytest.raises(HydrationNoteError, match="actual integer"):
        hydrate_audio(True, anki=anki)

    assert anki.notes_calls == 0


@pytest.mark.parametrize("result", [[], [{"noteId": NOTE_ID}], [{}, {}]])
def test_loaded_note_requires_exactly_one_well_shaped_note(result: object) -> None:
    anki = FakeAnki(make_unit())
    anki.notes_result_override = result

    with pytest.raises(HydrationNoteError):
        hydrate_audio(NOTE_ID, anki=anki)


def test_forge_precondition_fails_before_audio_work() -> None:
    unit = make_unit()
    unit.definition_en = ""
    anki = FakeAnki(unit)
    synth = FakeSynthesizer()

    with pytest.raises(HydrationCoreInvalidError) as captured:
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert captured.value.violations == ("F_DEFINITION_EMPTY",)
    assert synth.calls == []
    assert anki.retrieve_calls == []
    assert anki.store_calls == []
    assert anki.updates == []


def test_context_bank_must_be_complete_and_valid() -> None:
    unit = make_unit(contexts_ready=False)
    anki = FakeAnki(unit)

    with pytest.raises(AudioContextNotReadyError):
        hydrate_audio(NOTE_ID, anki=anki)

    unit = make_unit()
    unit.Ctx_4 = "This sentence lacks the required vocabulary item entirely."
    with pytest.raises(AudioContextNotReadyError):
        hydrate_audio(NOTE_ID, anki=FakeAnki(unit))


def test_target_l_disabled_performs_zero_audio_operations() -> None:
    unit = make_unit(target_l=False)
    unit.audio_1 = "arbitrary malformed legacy value"
    unit.audio_2 = "opaque two"
    unit.audio_3 = "opaque three"
    anki = FakeAnki(unit)
    synth = FakeSynthesizer()

    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=FROZEN_TTS_CONFIG,
    )

    assert outcome is AudioOutcome.SKIPPED_NO_L
    assert synth.identity_reads == 0
    assert synth.calls == []
    assert anki.retrieve_calls == []
    assert anki.store_calls == []
    assert anki.updates == []


def test_existing_valid_audio_is_already_ready_despite_runtime_drift() -> None:
    unit = make_unit()
    filename = "vocab-a1-1111111111111111.mp3"
    unit.audio_1 = sound_markup(filename)
    anki = FakeAnki(unit)
    anki.media[filename] = b"accepted-legacy-provider-bytes"

    class ExplodingIdentity:
        @property
        def synthesis_identity(self) -> object:
            raise AssertionError("accepted audio must not inspect runtime identity")

    drifted = replace(FROZEN_TTS_CONFIG, provider="changed-local-runtime")
    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=ExplodingIdentity(),
        tts_config=drifted,
    )

    assert outcome is AudioOutcome.ALREADY_READY
    assert anki.retrieve_calls == [filename]
    assert anki.store_calls == []
    assert anki.updates == []


def test_existing_audio_needs_no_current_runtime_dependencies() -> None:
    unit = make_unit()
    filename = "vocab-a1-2222222222222222.mp3"
    unit.audio_1 = sound_markup(filename)
    anki = FakeAnki(unit)
    anki.media[filename] = b"accepted"

    assert hydrate_audio(NOTE_ID, anki=anki) is AudioOutcome.ALREADY_READY


@pytest.mark.parametrize(
    "markup",
    [
        "arbitrary.mp3",
        "[sound:vocab-a2-0000000000000000.mp3]",
        "[sound:vocab-a1-ABCDEF0000000000.mp3]",
    ],
)
def test_existing_malformed_audio1_fails_closed(markup: str) -> None:
    unit = make_unit()
    unit.audio_1 = markup
    anki = FakeAnki(unit)

    with pytest.raises(AudioExistingInvalidError):
        hydrate_audio(NOTE_ID, anki=anki)

    assert anki.store_calls == []
    assert anki.updates == []


@pytest.mark.parametrize("media", [None, b"", "not-bytes"])
def test_existing_audio1_missing_or_empty_media_fails_without_repair(
    media: object,
) -> None:
    unit = make_unit()
    filename = "vocab-a1-3333333333333333.mp3"
    unit.audio_1 = sound_markup(filename)
    anki = FakeAnki(unit)
    if media is not None:
        anki.media[filename] = media

    with pytest.raises(AudioMediaMissingOrInvalidError):
        hydrate_audio(NOTE_ID, anki=anki)

    assert anki.store_calls == []
    assert anki.updates == []


def test_matching_frozen_identity_synthesizes_exact_ctx1_and_updates_only_audio1() -> None:
    unit = make_unit()
    anki = FakeAnki(unit)
    synth = FakeSynthesizer()
    filename = expected_filename(unit)

    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=FROZEN_TTS_CONFIG,
    )

    assert outcome is AudioOutcome.CREATED
    assert synth.calls == [unit.Ctx_1]
    assert anki.retrieve_calls == [filename, filename]
    assert anki.store_calls == [(filename, b"local-mp3")]
    assert anki.updates == [(NOTE_ID, {"audio_1": sound_markup(filename)})]
    assert anki.values["VisualCue"] == "keep-this-cue"


def test_existing_exact_deterministic_media_is_reused_without_synthesis() -> None:
    unit = make_unit()
    filename = expected_filename(unit)
    anki = FakeAnki(unit)
    anki.media[filename] = b"orphan-from-earlier-safe-run"
    synth = FakeSynthesizer()

    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=synth,
        tts_config=FROZEN_TTS_CONFIG,
    )

    assert outcome is AudioOutcome.CREATED
    assert anki.retrieve_calls == [filename]
    assert synth.calls == []
    assert anki.store_calls == []
    assert anki.updates == [(NOTE_ID, {"audio_1": sound_markup(filename)})]


@pytest.mark.parametrize("invalid_media", [b"", "not-bytes"])
def test_invalid_deterministic_media_fails_before_synthesis_or_write(
    invalid_media: object,
) -> None:
    unit = make_unit()
    filename = expected_filename(unit)
    anki = FakeAnki(unit)
    anki.media[filename] = invalid_media
    synth = FakeSynthesizer()

    with pytest.raises(AudioMediaMissingOrInvalidError):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert synth.calls == []
    assert anki.store_calls == []
    assert anki.updates == []


@pytest.mark.parametrize(
    "identity",
    [
        replace(FROZEN_TTS_CONFIG, provider="wrong-provider"),
        replace(FROZEN_TTS_CONFIG, output_format="wrong-format"),
        replace(FROZEN_TTS_CONFIG, voice_id="wrong-voice"),
        None,
        "malformed",
    ],
)
def test_synthesizer_identity_mismatch_fails_before_every_audio_side_effect(
    identity: object,
) -> None:
    anki = FakeAnki(make_unit())
    synth = FakeSynthesizer(identity)

    with pytest.raises(AudioSynthesisIdentityError):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert anki.retrieve_calls == []
    assert synth.calls == []
    assert anki.store_calls == []
    assert anki.updates == []


def test_missing_synthesis_identity_fails_before_every_audio_side_effect() -> None:
    anki = FakeAnki(make_unit())

    with pytest.raises(AudioSynthesisIdentityError, match="missing"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=object(),
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert anki.retrieve_calls == []
    assert anki.store_calls == []
    assert anki.updates == []


def test_drifted_tts_config_fails_before_every_audio_side_effect() -> None:
    anki = FakeAnki(make_unit())
    synth = FakeSynthesizer()
    drifted = replace(FROZEN_TTS_CONFIG, encoder_version="different")

    with pytest.raises(AudioSynthesisIdentityError, match="TtsConfig"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=synth,
            tts_config=drifted,
        )

    assert anki.retrieve_calls == []
    assert synth.calls == []
    assert anki.store_calls == []
    assert anki.updates == []


@pytest.mark.parametrize(
    ("field_name", "opaque_value"),
    [
        ("audio_2", "[not even valid markup"),
        ("audio_3", "opaque legacy value exactly preserved"),
    ],
)
def test_reserved_audio_values_do_not_block_audio1_and_are_preserved(
    field_name: str,
    opaque_value: str,
) -> None:
    unit = make_unit()
    setattr(unit, field_name, opaque_value)
    anki = FakeAnki(unit)

    assert hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=FakeSynthesizer(),
        tts_config=FROZEN_TTS_CONFIG,
    ) is AudioOutcome.CREATED

    assert anki.values[field_name] == opaque_value
    assert all(set(fields) == {"audio_1"} for _note_id, fields in anki.updates)


def test_reserved_audio_changes_are_not_part_of_stale_identity() -> None:
    unit = make_unit()
    unit.audio_2 = "before"
    anki = FakeAnki(unit)
    anki.notes_hooks[2] = lambda fake: fake.values.update(
        {"audio_2": "human changed opaque value"}
    )

    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=FakeSynthesizer(),
        tts_config=FROZEN_TTS_CONFIG,
    )

    assert outcome is AudioOutcome.CREATED
    assert anki.values["audio_2"] == "human changed opaque value"
    assert set(anki.updates[0][1]) == {"audio_1"}


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("Target_L", ""),
        ("Ctx_1", "A human changed this subtle context before commit."),
        ("Ctx_2", "A human changed another subtle validated context."),
        ("definition_en", "a human changed the intended sense"),
        ("audio_1", "[sound:vocab-a1-ffffffffffffffff.mp3]"),
    ],
)
def test_relevant_stale_guard_prevents_audio1_field_write(
    field_name: str,
    replacement: str,
) -> None:
    anki = FakeAnki(make_unit())
    anki.notes_hooks[2] = lambda fake: fake.values.update(
        {field_name: replacement}
    )

    outcome = hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=FakeSynthesizer(),
        tts_config=FROZEN_TTS_CONFIG,
    )

    assert outcome is AudioOutcome.STALE
    assert anki.store_calls
    assert anki.updates == []


def test_store_filename_or_media_readback_mismatch_fails_before_field_write() -> None:
    anki = FakeAnki(make_unit())
    anki.store_name_override = "renamed.mp3"
    with pytest.raises(AudioPersistenceError, match="filename"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=FROZEN_TTS_CONFIG,
        )
    assert anki.updates == []

    anki = FakeAnki(make_unit())
    anki.corrupt_store_readback = True
    with pytest.raises(AudioPersistenceError, match="bytes"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=FROZEN_TTS_CONFIG,
        )
    assert anki.updates == []


@pytest.mark.parametrize("audio", [b"", None, "not-bytes"])
def test_synthesizer_output_must_be_nonempty_bytes(audio: object) -> None:
    anki = FakeAnki(make_unit())

    with pytest.raises(Exception, match="non-empty bytes"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(audio=audio),
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert anki.store_calls == []
    assert anki.updates == []


def test_audio1_readback_mismatch_is_persistence_failure() -> None:
    anki = FakeAnki(make_unit())
    anki.apply_updates = False

    with pytest.raises(AudioPersistenceError, match="audio_1"):
        hydrate_audio(
            NOTE_ID,
            anki=anki,
            synthesizer=FakeSynthesizer(),
            tts_config=FROZEN_TTS_CONFIG,
        )

    assert anki.updates and set(anki.updates[0][1]) == {"audio_1"}


def test_hydration_never_uses_full_replacement_serialization(monkeypatch) -> None:
    anki = FakeAnki(make_unit())

    def forbidden_to_note_fields(_self):
        raise AssertionError("T8 must not call to_note_fields")

    monkeypatch.setattr(VocabUnit, "to_note_fields", forbidden_to_note_fields)

    assert hydrate_audio(
        NOTE_ID,
        anki=anki,
        synthesizer=FakeSynthesizer(),
        tts_config=FROZEN_TTS_CONFIG,
    ) is AudioOutcome.CREATED
