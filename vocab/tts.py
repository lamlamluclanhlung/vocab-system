"""Pure local-TTS configuration, request identity, and Anki markup helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Protocol

from .media_contract import (
    ACTIVE_AUDIO_SLOT,
    AUDIO_BIT_RATE_KBPS,
    AUDIO_CHANNELS,
    AUDIO_ENCODER_ID,
    AUDIO_ENCODER_QUALITY,
    AUDIO_ENCODER_VERSION,
    AUDIO_FILENAME_PATTERN,
    AUDIO_OUTPUT_FORMAT,
    AUDIO_PCM_FORMAT,
    AUDIO_PROVIDER_ID,
    AUDIO_REQUEST_VERSION,
    AUDIO_SAMPLE_RATE,
    AUDIO_SOURCE_CONTEXT_FIELD,
    KOKORO_INFERENCE_DEVICE,
    KOKORO_LANG_CODE,
    KOKORO_MODEL_ID,
    KOKORO_MODEL_REVISION,
    KOKORO_MODEL_SHA256,
    KOKORO_PACKAGE_VERSION,
    KOKORO_SPEED,
    KOKORO_VOICE_ID,
    KOKORO_VOICE_SHA256,
)


_AUDIO_FILENAME_RE = re.compile(AUDIO_FILENAME_PATTERN)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TtsContractError(ValueError):
    """Raised when TTS configuration or persisted markup is invalid."""


@dataclass(frozen=True, slots=True)
class TtsConfig:
    """Identity metadata for exactly one local audio_1 synthesis setup."""

    provider: str = AUDIO_PROVIDER_ID
    kokoro_package_version: str = KOKORO_PACKAGE_VERSION
    model_id: str = KOKORO_MODEL_ID
    model_revision: str = KOKORO_MODEL_REVISION
    model_sha256: str = KOKORO_MODEL_SHA256
    voice_id: str = KOKORO_VOICE_ID
    voice_sha256: str = KOKORO_VOICE_SHA256
    lang_code: str = KOKORO_LANG_CODE
    speed: float = KOKORO_SPEED
    inference_device: str = KOKORO_INFERENCE_DEVICE
    sample_rate: int = AUDIO_SAMPLE_RATE
    channels: int = AUDIO_CHANNELS
    pcm_format: str = AUDIO_PCM_FORMAT
    encoder_id: str = AUDIO_ENCODER_ID
    encoder_version: str = AUDIO_ENCODER_VERSION
    bit_rate_kbps: int = AUDIO_BIT_RATE_KBPS
    encoder_quality: int = AUDIO_ENCODER_QUALITY
    output_format: str = AUDIO_OUTPUT_FORMAT

    def __post_init__(self) -> None:
        for field_name in (
            "provider",
            "kokoro_package_version",
            "model_id",
            "model_revision",
            "model_sha256",
            "voice_id",
            "voice_sha256",
            "lang_code",
            "inference_device",
            "pcm_format",
            "encoder_id",
            "encoder_version",
            "output_format",
        ):
            _require_exact_nonempty(field_name, getattr(self, field_name))
        for field_name in ("model_sha256", "voice_sha256"):
            if _SHA256_RE.fullmatch(getattr(self, field_name)) is None:
                raise TtsContractError(
                    f"{field_name} must be 64 lowercase hexadecimal characters"
                )
        if type(self.speed) is not float:
            raise TypeError("speed must be a float")
        if not math.isfinite(self.speed) or self.speed <= 0:
            raise TtsContractError("speed must be finite and positive")
        for field_name in ("sample_rate", "channels", "bit_rate_kbps"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value <= 0:
                raise TtsContractError(f"{field_name} must be positive")
        if type(self.encoder_quality) is not int:
            raise TypeError("encoder_quality must be an integer")
        if not 0 <= self.encoder_quality <= 9:
            raise TtsContractError("encoder_quality must be between 0 and 9")


class SpeechSynthesizer(Protocol):
    """Provider-neutral boundary for one immutable local synthesizer."""

    @property
    def synthesis_identity(self) -> TtsConfig: ...

    def synthesize(self, *, text: str) -> bytes: ...


def synthesis_request(
    *,
    config: TtsConfig,
    unit_key: str,
    text: str,
    slot: int = ACTIVE_AUDIO_SLOT,
    version: int = AUDIO_REQUEST_VERSION,
    source_context_field: str = AUDIO_SOURCE_CONTEXT_FIELD,
) -> dict[str, object]:
    """Build the exact D32 logical synthesis request whose identity is hashed."""
    if type(config) is not TtsConfig:
        raise TypeError("config must be a TtsConfig")
    _require_exact_nonempty("unit_key", unit_key)
    if type(slot) is not int or slot != ACTIVE_AUDIO_SLOT:
        raise TtsContractError("slot must be exactly 1")
    if type(text) is not str:
        raise TypeError("text must be a string")
    if text == "":
        raise TtsContractError("text must be non-empty")
    if type(version) is not int or version < 1:
        raise TtsContractError("version must be a positive integer")
    if source_context_field != AUDIO_SOURCE_CONTEXT_FIELD:
        raise TtsContractError("source_context_field must be exactly Ctx_1")

    return {
        "v": version,
        "provider": config.provider,
        "kokoro_package_version": config.kokoro_package_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "model_sha256": config.model_sha256,
        "voice_id": config.voice_id,
        "voice_sha256": config.voice_sha256,
        "lang_code": config.lang_code,
        "speed": config.speed,
        "inference_device": config.inference_device,
        "sample_rate": config.sample_rate,
        "channels": config.channels,
        "pcm_format": config.pcm_format,
        "encoder_id": config.encoder_id,
        "encoder_version": config.encoder_version,
        "bit_rate_kbps": config.bit_rate_kbps,
        "encoder_quality": config.encoder_quality,
        "output_format": config.output_format,
        "unit_key": unit_key,
        "slot": slot,
        "source_context_field": source_context_field,
        "text": text,
    }


def canonical_synthesis_request_bytes(
    *,
    config: TtsConfig,
    unit_key: str,
    text: str,
    slot: int = ACTIVE_AUDIO_SLOT,
    version: int = AUDIO_REQUEST_VERSION,
    source_context_field: str = AUDIO_SOURCE_CONTEXT_FIELD,
) -> bytes:
    """Serialize one logical synthesis request with canonical JSON."""
    request = synthesis_request(
        config=config,
        unit_key=unit_key,
        text=text,
        slot=slot,
        version=version,
        source_context_field=source_context_field,
    )
    return json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def deterministic_audio_filename(
    *,
    config: TtsConfig,
    unit_key: str,
    text: str,
    slot: int = ACTIVE_AUDIO_SLOT,
    version: int = AUDIO_REQUEST_VERSION,
    source_context_field: str = AUDIO_SOURCE_CONTEXT_FIELD,
) -> str:
    """Return the immutable request-identified audio_1 filename."""
    canonical_bytes = canonical_synthesis_request_bytes(
        config=config,
        unit_key=unit_key,
        text=text,
        slot=slot,
        version=version,
        source_context_field=source_context_field,
    )
    identity = hashlib.sha256(canonical_bytes).hexdigest()[:16]
    return f"vocab-a1-{identity}.mp3"


def sound_markup(filename: str) -> str:
    """Build strict Anki sound markup for a valid audio_1 filename."""
    if type(filename) is not str:
        raise TypeError("filename must be a string")
    if _AUDIO_FILENAME_RE.fullmatch(filename) is None:
        raise TtsContractError("filename is not a valid audio_1 filename")
    return f"[sound:{filename}]"


def parse_audio1_sound_markup(value: str) -> str:
    """Return the filename from strict audio_1 markup using one grammar."""
    if type(value) is not str:
        raise TypeError("audio_1 value must be a string")
    prefix = "[sound:"
    suffix = "]"
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise TtsContractError("audio_1 is not strict Anki sound markup")
    filename = value[len(prefix) : -len(suffix)]
    if _AUDIO_FILENAME_RE.fullmatch(filename) is None:
        raise TtsContractError("audio_1 filename is not a valid T8 artifact")
    return filename


def _require_exact_nonempty(name: str, value: object) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or not value.strip():
        raise TtsContractError(f"{name} must be non-empty")
    if value != value.strip():
        raise TtsContractError(
            f"{name} must not contain leading or trailing whitespace"
        )


FROZEN_TTS_CONFIG = TtsConfig()
