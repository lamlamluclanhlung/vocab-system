"""Pure tests for T8 TTS configuration and media identity."""

from __future__ import annotations

import builtins
import json
import re

import pytest

from vocab.media_contract import AUDIO_FILENAME_PATTERN
from vocab.tts import (
    TtsConfig,
    TtsContractError,
    canonical_synthesis_request_bytes,
    deterministic_audio_filename,
    parse_t8_sound_markup,
    sound_markup,
)


def request_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "region": "southeastasia",
        "unit_key": "subtle::small-difference",
        "slot": 1,
        "text": "The subtle change became clear after careful comparison.",
        "voice_id": "en-US-Voice-One",
        "locale": "en-US",
    }
    values.update(changes)
    return values


@pytest.mark.parametrize(
    "voices",
    [
        ("one", "two"),
        ("one", "two", "three", "four"),
        ["one", "two", "three"],
    ],
)
def test_config_requires_exact_tuple_of_three_voices(voices) -> None:
    with pytest.raises((TypeError, TtsContractError)):
        TtsConfig("southeastasia", "en-US", voices)


def test_config_requires_distinct_voices() -> None:
    with pytest.raises(TtsContractError, match="distinct"):
        TtsConfig("southeastasia", "en-US", ("one", "one", "three"))


@pytest.mark.parametrize(
    ("region", "locale", "voices"),
    [
        ("", "en-US", ("one", "two", "three")),
        ("southeastasia", "", ("one", "two", "three")),
        ("southeastasia", "en-US", ("one", "", "three")),
        ("   ", "en-US", ("one", "two", "three")),
    ],
)
def test_config_rejects_empty_values(region, locale, voices) -> None:
    with pytest.raises(TtsContractError):
        TtsConfig(region, locale, voices)


@pytest.mark.parametrize(
    ("region", "locale", "voices"),
    [
        (" southeastasia", "en-US", ("one", "two", "three")),
        ("southeastasia", "en-US ", ("one", "two", "three")),
        ("southeastasia", "en-US", ("one ", "two", "three")),
    ],
)
def test_config_does_not_silently_trim(region, locale, voices) -> None:
    with pytest.raises(TtsContractError, match="whitespace"):
        TtsConfig(region, locale, voices)


def test_canonical_request_contains_exact_identity_fields() -> None:
    canonical = canonical_synthesis_request_bytes(**request_values())

    assert json.loads(canonical.decode("utf-8")) == {
        "v": 1,
        "provider": "azure-speech-rest",
        "region": "southeastasia",
        "unit_key": "subtle::small-difference",
        "slot": 1,
        "source_context_field": "Ctx_1",
        "text": "The subtle change became clear after careful comparison.",
        "voice_id": "en-US-Voice-One",
        "locale": "en-US",
        "output_format": "audio-24khz-48kbitrate-mono-mp3",
    }
    assert canonical == json.dumps(
        json.loads(canonical.decode("utf-8")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_same_logical_request_has_same_bytes_and_filename() -> None:
    values = request_values()

    assert canonical_synthesis_request_bytes(
        **values
    ) == canonical_synthesis_request_bytes(**values)
    assert deterministic_audio_filename(
        **values
    ) == deterministic_audio_filename(**values)


def test_identity_does_not_use_python_builtin_hash(monkeypatch) -> None:
    def forbidden_hash(_value):
        raise AssertionError("built-in hash() must not define media identity")

    monkeypatch.setattr(builtins, "hash", forbidden_hash)

    assert re.fullmatch(
        AUDIO_FILENAME_PATTERN,
        deterministic_audio_filename(**request_values()),
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("unit_key", "subtle::another-sense"),
        ("slot", 2),
        ("voice_id", "en-US-Voice-Two"),
        ("text", "A different subtle context appears in this sentence."),
        ("locale", "en-GB"),
        ("region", "eastus"),
        ("version", 2),
        ("output_format", "another-format"),
    ],
)
def test_identity_changes_when_logical_request_changes(
    field_name: str,
    replacement: object,
) -> None:
    baseline = deterministic_audio_filename(**request_values())

    changed = deterministic_audio_filename(
        **request_values(**{field_name: replacement})
    )

    assert changed != baseline


@pytest.mark.parametrize("slot", [1, 2, 3])
def test_filename_matches_exact_slot_pattern_without_raw_unit_key(slot: int) -> None:
    filename = deterministic_audio_filename(
        **request_values(slot=slot, voice_id=f"voice-{slot}")
    )

    match = re.fullmatch(AUDIO_FILENAME_PATTERN, filename)
    assert match is not None
    assert match.group(1) == str(slot)
    assert "subtle" not in filename
    assert "::" not in filename


def test_sound_markup_is_exact_and_correct_slot_parser_accepts_it() -> None:
    filename = deterministic_audio_filename(**request_values(slot=1))
    markup = sound_markup(filename)

    assert markup == f"[sound:{filename}]"
    assert parse_t8_sound_markup("audio_1", markup) == filename


def test_sound_parser_rejects_wrong_slot() -> None:
    filename = deterministic_audio_filename(**request_values(slot=2))

    with pytest.raises(TtsContractError, match="slot"):
        parse_t8_sound_markup("audio_1", sound_markup(filename))


@pytest.mark.parametrize(
    "value",
    [
        "[sound:folder/vocab-a1-0123456789abcdef.mp3]",
        "vocab-a1-0123456789abcdef.mp3",
        "[sound:vocab-a1-0123456789ABCDEG.mp3]",
        "[sound:vocab-a4-0123456789abcdef.mp3]",
    ],
)
def test_sound_parser_rejects_paths_and_malformed_markup(value: str) -> None:
    with pytest.raises(TtsContractError):
        parse_t8_sound_markup("audio_1", value)
