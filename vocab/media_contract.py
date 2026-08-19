"""Human-owned semantic contract for T8 audio artifacts.

This module contains immutable local-media identities and review semantics
only.  It performs no I/O, synthesis, persistence, validation, or repair.
"""

from __future__ import annotations

from typing import Final


NORMAL_REVIEW_AUDIO_FIELD: Final[str] = "audio_1"

RESERVED_AUDIO_FIELDS: Final[tuple[str, ...]] = (
    "audio_2",
    "audio_3",
)

ACTIVE_AUDIO_SLOT: Final[int] = 1
AUDIO_SOURCE_CONTEXT_FIELD: Final[str] = "Ctx_1"

AUDIO_REQUEST_VERSION: Final[int] = 2
AUDIO_PROVIDER_ID: Final[str] = "kokoro-local"

KOKORO_PACKAGE_VERSION: Final[str] = "0.9.4"
KOKORO_MODEL_ID: Final[str] = "hexgrad/Kokoro-82M"
KOKORO_MODEL_REVISION: Final[str] = (
    "8542409da2986c0ab5d41b3cf0411f7a58caab38"
)
KOKORO_MODEL_FILENAME: Final[str] = "kokoro-v1_0.pth"
KOKORO_MODEL_SHA256: Final[str] = (
    "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"
)
KOKORO_CONFIG_FILENAME: Final[str] = "config.json"

KOKORO_VOICE_ID: Final[str] = "af_heart"
KOKORO_VOICE_FILENAME: Final[str] = "voices/af_heart.pt"
KOKORO_VOICE_SHA256: Final[str] = (
    "0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff"
)
KOKORO_LANG_CODE: Final[str] = "a"
KOKORO_SPEED: Final[float] = 1.0
KOKORO_INFERENCE_DEVICE: Final[str] = "cpu"

AUDIO_SAMPLE_RATE: Final[int] = 24_000
AUDIO_CHANNELS: Final[int] = 1
AUDIO_PCM_FORMAT: Final[str] = "s16le"

AUDIO_ENCODER_ID: Final[str] = "lameenc"
AUDIO_ENCODER_VERSION: Final[str] = "1.8.4"
AUDIO_BIT_RATE_KBPS: Final[int] = 48
AUDIO_ENCODER_QUALITY: Final[int] = 2
AUDIO_OUTPUT_FORMAT: Final[str] = "mp3-48kbps-24khz-mono-s16le"

# New and legacy accepted audio_1 artifacts share this high-level grammar.
AUDIO_FILENAME_PATTERN: Final[str] = (
    r"^vocab-a1-[0-9a-f]{16}\.mp3$"
)
