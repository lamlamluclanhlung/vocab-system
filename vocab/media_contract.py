"""Human-owned semantic contract for T8 audio artifacts.

This module contains media identities and review semantics only.  It performs
no I/O, provider calls, persistence, validation, or automatic repair.
"""

from __future__ import annotations

from typing import Final


NORMAL_REVIEW_AUDIO_FIELD: Final[str] = "audio_1"

NOVEL_AUDIO_FIELDS: Final[tuple[str, ...]] = (
    "audio_2",
    "audio_3",
)

AUDIO_SLOT_FIELDS: Final[tuple[str, ...]] = (
    NORMAL_REVIEW_AUDIO_FIELD,
    *NOVEL_AUDIO_FIELDS,
)

AUDIO_SOURCE_CONTEXT_FIELD: Final[str] = "Ctx_1"

AUDIO_SLOT_NUMBERS: Final[tuple[int, ...]] = (1, 2, 3)

AUDIO_REQUEST_VERSION: Final[int] = 1

AUDIO_PROVIDER_ID: Final[str] = "azure-speech-rest"

AUDIO_OUTPUT_FORMAT: Final[str] = (
    "audio-24khz-48kbitrate-mono-mp3"
)

AUDIO_FILENAME_PATTERN: Final[str] = (
    r"^vocab-a([123])-[0-9a-f]{16}\.mp3$"
)
