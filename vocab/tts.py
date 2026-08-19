"""Pure TTS configuration, request identity, and Anki markup helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from .media_contract import (
    AUDIO_FILENAME_PATTERN,
    AUDIO_OUTPUT_FORMAT,
    AUDIO_PROVIDER_ID,
    AUDIO_REQUEST_VERSION,
    AUDIO_SLOT_FIELDS,
    AUDIO_SLOT_NUMBERS,
    AUDIO_SOURCE_CONTEXT_FIELD,
)


_AUDIO_FILENAME_RE = re.compile(AUDIO_FILENAME_PATTERN)
_SOUND_MARKUP_RE = re.compile(
    r"^\[sound:(vocab-a([123])-[0-9a-f]{16}\.mp3)\]$"
)


class TtsContractError(ValueError):
    """Raised when TTS configuration or persisted markup is invalid."""


class SpeechSynthesizer(Protocol):
    """Provider-neutral speech synthesis boundary."""

    def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        locale: str,
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class TtsConfig:
    """One fixed region/locale and exactly three ordered distinct voices."""

    region: str
    locale: str
    voice_ids: tuple[str, str, str]

    def __post_init__(self) -> None:
        _require_exact_nonempty("region", self.region)
        _require_exact_nonempty("locale", self.locale)
        if not isinstance(self.voice_ids, tuple):
            raise TypeError("voice_ids must be a tuple")
        if len(self.voice_ids) != len(AUDIO_SLOT_NUMBERS):
            raise TtsContractError("voice_ids must contain exactly three voices")
        for index, voice_id in enumerate(self.voice_ids, start=1):
            _require_exact_nonempty(f"voice_ids[{index - 1}]", voice_id)
        if len(set(self.voice_ids)) != len(self.voice_ids):
            raise TtsContractError("voice_ids must be distinct")


def synthesis_request(
    *,
    region: str,
    unit_key: str,
    slot: int,
    text: str,
    voice_id: str,
    locale: str,
    version: int = AUDIO_REQUEST_VERSION,
    provider: str = AUDIO_PROVIDER_ID,
    output_format: str = AUDIO_OUTPUT_FORMAT,
) -> dict[str, object]:
    """Build the exact logical synthesis request whose identity is hashed."""
    _require_exact_nonempty("region", region)
    _require_exact_nonempty("unit_key", unit_key)
    if type(slot) is not int or slot not in AUDIO_SLOT_NUMBERS:
        raise TtsContractError("slot must be one of 1, 2, or 3")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if text == "":
        raise TtsContractError("text must be non-empty")
    _require_exact_nonempty("voice_id", voice_id)
    _require_exact_nonempty("locale", locale)
    if type(version) is not int or version < 1:
        raise TtsContractError("version must be a positive integer")
    _require_exact_nonempty("provider", provider)
    _require_exact_nonempty("output_format", output_format)

    return {
        "v": version,
        "provider": provider,
        "region": region,
        "unit_key": unit_key,
        "slot": slot,
        "source_context_field": AUDIO_SOURCE_CONTEXT_FIELD,
        "text": text,
        "voice_id": voice_id,
        "locale": locale,
        "output_format": output_format,
    }


def canonical_synthesis_request_bytes(**request_values: object) -> bytes:
    """Serialize one logical synthesis request with the frozen canonical JSON."""
    request = synthesis_request(**request_values)  # type: ignore[arg-type]
    return json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def deterministic_audio_filename(**request_values: object) -> str:
    """Return the immutable request-identified filename for one audio slot."""
    canonical_bytes = canonical_synthesis_request_bytes(**request_values)
    request = json.loads(canonical_bytes.decode("utf-8"))
    slot = request["slot"]
    identity = hashlib.sha256(canonical_bytes).hexdigest()[:16]
    return f"vocab-a{slot}-{identity}.mp3"


def sound_markup(filename: str) -> str:
    """Build strict Anki sound markup for a valid T8 filename."""
    if not isinstance(filename, str):
        raise TypeError("filename must be a string")
    if _AUDIO_FILENAME_RE.fullmatch(filename) is None:
        raise TtsContractError("filename is not a valid T8 audio filename")
    return f"[sound:{filename}]"


def parse_t8_sound_markup(field_name: str, value: str) -> str:
    """Return the referenced filename after strict slot-specific validation."""
    if field_name not in AUDIO_SLOT_FIELDS:
        raise TtsContractError("field_name is not a T8 audio slot")
    if not isinstance(value, str):
        raise TypeError("audio field value must be a string")
    match = _SOUND_MARKUP_RE.fullmatch(value)
    if match is None:
        raise TtsContractError("audio field is not strict T8 sound markup")
    expected_slot = AUDIO_SLOT_FIELDS.index(field_name) + 1
    actual_slot = int(match.group(2))
    if actual_slot != expected_slot:
        raise TtsContractError("audio filename slot does not match its field")
    return match.group(1)


def _require_exact_nonempty(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or not value.strip():
        raise TtsContractError(f"{name} must be non-empty")
    if value != value.strip():
        raise TtsContractError(
            f"{name} must not contain leading or trailing whitespace"
        )
