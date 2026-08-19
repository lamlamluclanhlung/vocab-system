"""Pure tests for the T8.1 local-TTS contract and media identity."""

from __future__ import annotations

import json
import re
from dataclasses import fields, replace

import pytest

from vocab.media_contract import AUDIO_FILENAME_PATTERN
from vocab.tts import (
    FROZEN_TTS_CONFIG,
    TtsConfig,
    TtsContractError,
    canonical_synthesis_request_bytes,
    deterministic_audio_filename,
    parse_audio1_sound_markup,
    sound_markup,
    synthesis_request,
)


UNIT_KEY = "subtle::small-difference"
TEXT = "The subtle change became clear after careful comparison."


def test_config_has_one_voice_and_no_cloud_or_three_voice_fields() -> None:
    names = tuple(field.name for field in fields(TtsConfig))

    assert "voice_id" in names
    assert "voice_ids" not in names
    assert "region" not in names
    assert "locale" not in names


def test_canonical_request_contains_exact_d32_identity_fields() -> None:
    request = synthesis_request(
        config=FROZEN_TTS_CONFIG,
        unit_key=UNIT_KEY,
        text=TEXT,
    )

    assert set(request) == {
        "v",
        "provider",
        "kokoro_package_version",
        "model_id",
        "model_revision",
        "model_sha256",
        "voice_id",
        "voice_sha256",
        "lang_code",
        "speed",
        "inference_device",
        "sample_rate",
        "channels",
        "pcm_format",
        "encoder_id",
        "encoder_version",
        "bit_rate_kbps",
        "encoder_quality",
        "output_format",
        "unit_key",
        "slot",
        "source_context_field",
        "text",
    }
    assert request == {
        "v": 2,
        "provider": "kokoro-local",
        "kokoro_package_version": "0.9.4",
        "model_id": "hexgrad/Kokoro-82M",
        "model_revision": "8542409da2986c0ab5d41b3cf0411f7a58caab38",
        "model_sha256": "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4",
        "voice_id": "af_heart",
        "voice_sha256": "0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff",
        "lang_code": "a",
        "speed": 1.0,
        "inference_device": "cpu",
        "sample_rate": 24000,
        "channels": 1,
        "pcm_format": "s16le",
        "encoder_id": "lameenc",
        "encoder_version": "1.8.4",
        "bit_rate_kbps": 48,
        "encoder_quality": 2,
        "output_format": "mp3-48kbps-24khz-mono-s16le",
        "unit_key": UNIT_KEY,
        "slot": 1,
        "source_context_field": "Ctx_1",
        "text": TEXT,
    }


def test_canonical_bytes_and_filename_are_stable() -> None:
    canonical = canonical_synthesis_request_bytes(
        config=FROZEN_TTS_CONFIG,
        unit_key=UNIT_KEY,
        text=TEXT,
    )

    assert canonical == json.dumps(
        json.loads(canonical.decode("utf-8")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert deterministic_audio_filename(
        config=FROZEN_TTS_CONFIG,
        unit_key=UNIT_KEY,
        text=TEXT,
    ) == "vocab-a1-426a4ab56d9a7d7f.mp3"


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("provider", "other-local"),
        ("kokoro_package_version", "0.9.5"),
        ("model_id", "other/model"),
        ("model_revision", "different-revision"),
        ("model_sha256", "1" * 64),
        ("voice_id", "af_bella"),
        ("voice_sha256", "2" * 64),
        ("lang_code", "b"),
        ("speed", 1.1),
        ("inference_device", "gpu"),
        ("sample_rate", 22050),
        ("channels", 2),
        ("pcm_format", "f32le"),
        ("encoder_id", "another-encoder"),
        ("encoder_version", "2.0"),
        ("bit_rate_kbps", 64),
        ("encoder_quality", 3),
        ("output_format", "different-format"),
    ],
)
def test_any_config_identity_change_changes_filename(
    field_name: str,
    replacement: object,
) -> None:
    baseline = deterministic_audio_filename(
        config=FROZEN_TTS_CONFIG,
        unit_key=UNIT_KEY,
        text=TEXT,
    )
    changed = deterministic_audio_filename(
        config=replace(FROZEN_TTS_CONFIG, **{field_name: replacement}),
        unit_key=UNIT_KEY,
        text=TEXT,
    )

    assert changed != baseline


@pytest.mark.parametrize(
    "changes",
    [
        {"unit_key": "subtle::another-sense"},
        {"text": "A different subtle context is persisted for review."},
        {"version": 3},
    ],
)
def test_request_identity_change_changes_filename(changes: dict[str, object]) -> None:
    baseline_values = {
        "config": FROZEN_TTS_CONFIG,
        "unit_key": UNIT_KEY,
        "text": TEXT,
    }
    baseline = deterministic_audio_filename(**baseline_values)
    baseline_values.update(changes)

    assert deterministic_audio_filename(**baseline_values) != baseline


@pytest.mark.parametrize("slot", [0, 2, 3, True, "1"])
def test_new_synthesis_rejects_every_slot_except_actual_integer_one(slot: object) -> None:
    with pytest.raises(TtsContractError, match="exactly 1"):
        deterministic_audio_filename(
            config=FROZEN_TTS_CONFIG,
            unit_key=UNIT_KEY,
            text=TEXT,
            slot=slot,
        )


def test_filename_and_markup_grammar_are_exact_and_shared() -> None:
    filename = deterministic_audio_filename(
        config=FROZEN_TTS_CONFIG,
        unit_key=UNIT_KEY,
        text=TEXT,
    )

    assert re.fullmatch(AUDIO_FILENAME_PATTERN, filename)
    assert sound_markup(filename) == f"[sound:{filename}]"
    assert parse_audio1_sound_markup(f"[sound:{filename}]") == filename


@pytest.mark.parametrize(
    "value",
    [
        "vocab-a1-0123456789abcdef.mp3",
        "[sound:folder/vocab-a1-0123456789abcdef.mp3]",
        "[sound:vocab-a1-0123456789ABCDEG.mp3]",
        "[sound:vocab-a2-0123456789abcdef.mp3]",
        " [sound:vocab-a1-0123456789abcdef.mp3]",
    ],
)
def test_audio1_markup_parser_rejects_every_non_exact_form(value: str) -> None:
    with pytest.raises(TtsContractError):
        parse_audio1_sound_markup(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": ""},
        {"model_sha256": "ABC"},
        {"voice_sha256": "0" * 63},
        {"speed": 1},
        {"speed": float("nan")},
        {"sample_rate": True},
        {"sample_rate": 0},
        {"channels": 0},
        {"bit_rate_kbps": -1},
        {"encoder_quality": 10},
    ],
)
def test_invalid_tts_config_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, TtsContractError)):
        replace(FROZEN_TTS_CONFIG, **changes)
