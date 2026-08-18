"""Human-owned semantic contract for normal Anki review templates.

This module derives T3 card-template invariants from the frozen vocabulary
contracts.  It contains no parser, verifier, I/O, or note-type mutation logic.
"""

from __future__ import annotations

from typing import Final

from .contracts import (
    ANKI_REVIEW_CONTEXT_FIELD,
    CARD_TEMPLATE_NAMES,
    CHANNEL_BY_TEMPLATE_NAME,
    NOTE_FIELDS,
    NOVEL_CONTEXT_FIELDS,
    TARGET_FIELD_BY_CHANNEL,
)


TARGET_FIELD_BY_TEMPLATE_NAME: Final[dict[str, str]] = {
    template_name: TARGET_FIELD_BY_CHANNEL[
        CHANNEL_BY_TEMPLATE_NAME[template_name]
    ]
    for template_name in CARD_TEMPLATE_NAMES
}

TARGET_TEMPLATE_FIELDS: Final[tuple[str, ...]] = tuple(
    TARGET_FIELD_BY_TEMPLATE_NAME[template_name]
    for template_name in CARD_TEMPLATE_NAMES
)

FORBIDDEN_NORMAL_REVIEW_FIELDS: Final[tuple[str, ...]] = (
    NOVEL_CONTEXT_FIELDS
)

REQUIRED_FRONT_FIELDS_BY_TEMPLATE_NAME: Final[
    dict[str, tuple[str, ...]]
] = {
    template_name: (
        (ANKI_REVIEW_CONTEXT_FIELD,)
        if CHANNEL_BY_TEMPLATE_NAME[template_name] == "R"
        else ()
    )
    for template_name in CARD_TEMPLATE_NAMES
}

PERSISTED_CARD_FIELDS: Final[tuple[str, ...]] = NOTE_FIELDS

# These names are rendered by Anki and are not persisted VocabularyUnit
# fields. No other virtual field is accepted implicitly.
ANKI_VIRTUAL_FIELDS: Final[tuple[str, ...]] = (
    "FrontSide",
    "Tags",
    "Type",
    "Deck",
    "Subdeck",
    "Card",
)
