"""Deterministic validation helpers for vocabulary units."""

from __future__ import annotations

import re
import unicodedata

from .contracts import (
    APOSTROPHE_EQUIVALENTS,
    CANONICAL_APOSTROPHE,
    CHUNK_MAX_INSERTED_TOKENS,
    FRAME_MIN_FIXED_TOKENS,
    FRAME_PLACEHOLDER,
    FRAME_PLACEHOLDER_COUNT,
    FRAME_SLOT_MAX_TOKENS,
    FRAME_SLOT_MIN_TOKENS,
    LEXICAL_TOKEN_PATTERN,
    TEXT_NORMALIZATION_FORM,
    UNIT_TYPE_VALUES,
)


_LEXICAL_TOKEN_RE = re.compile(LEXICAL_TOKEN_PATTERN)
_APOSTROPHE_TRANSLATION = str.maketrans(
    {apostrophe: CANONICAL_APOSTROPHE for apostrophe in APOSTROPHE_EQUIVALENTS}
)
_FRAME_PLACEHOLDER_TOKEN_RE = re.compile(
    rf"(?<![\w']){re.escape(FRAME_PLACEHOLDER)}(?![\w'])"
)


def normalize_tokens(text: str) -> tuple[str, ...]:
    """Return the deterministic lexical-token representation of *text*."""
    normalized = unicodedata.normalize(TEXT_NORMALIZATION_FORM, text)
    normalized = normalized.casefold()
    normalized = normalized.translate(_APOSTROPHE_TRANSLATION)
    return tuple(_LEXICAL_TOKEN_RE.findall(normalized))


def _contains_chunk(
    text_tokens: tuple[str, ...],
    target_tokens: tuple[str, ...],
) -> bool:
    """Match ordered target tokens within the configured insertion budget."""
    for start, text_token in enumerate(text_tokens):
        if text_token != target_tokens[0]:
            continue

        target_index = 1
        text_index = start + 1
        inserted_tokens = 0

        while target_index < len(target_tokens) and text_index < len(text_tokens):
            if text_tokens[text_index] == target_tokens[target_index]:
                target_index += 1
            else:
                inserted_tokens += 1
                if inserted_tokens > CHUNK_MAX_INSERTED_TOKENS:
                    break
            text_index += 1

        if target_index == len(target_tokens):
            return True

    return False


def _frame_parts(unit: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate a v0 frame and return its fixed tokens around the slot."""
    if unit.count(FRAME_PLACEHOLDER) != FRAME_PLACEHOLDER_COUNT:
        raise ValueError("frame unit must contain exactly one canonical placeholder")

    placeholder_match = _FRAME_PLACEHOLDER_TOKEN_RE.search(unit)
    if placeholder_match is None:
        raise ValueError("frame placeholder must be a standalone token")

    before_text, after_text = unit.split(FRAME_PLACEHOLDER)
    if "_" in before_text or "_" in after_text:
        raise ValueError("frame unit contains non-canonical placeholder syntax")

    before_tokens = normalize_tokens(before_text)
    after_tokens = normalize_tokens(after_text)
    if not before_tokens or not after_tokens:
        raise ValueError("frame unit must have fixed tokens on both sides of the slot")
    if len(before_tokens) + len(after_tokens) < FRAME_MIN_FIXED_TOKENS:
        raise ValueError("frame unit has too few fixed lexical tokens")

    return before_tokens, after_tokens


def _contains_frame(
    text_tokens: tuple[str, ...],
    before_tokens: tuple[str, ...],
    after_tokens: tuple[str, ...],
) -> bool:
    """Match contiguous fixed frame parts around a bounded lexical slot."""
    for start in range(len(text_tokens)):
        before_end = start + len(before_tokens)
        if text_tokens[start:before_end] != before_tokens:
            continue

        for slot_size in range(FRAME_SLOT_MIN_TOKENS, FRAME_SLOT_MAX_TOKENS + 1):
            after_start = before_end + slot_size
            after_end = after_start + len(after_tokens)
            if text_tokens[after_start:after_end] == after_tokens:
                return True

    return False


def contains_unit(text: str, unit: str, unit_type: str) -> bool:
    """Return whether *text* contains a valid normalized word, chunk, or frame."""
    if unit_type not in UNIT_TYPE_VALUES:
        raise ValueError(f"invalid unit_type: {unit_type!r}")

    text_tokens = normalize_tokens(text)

    if unit_type == "word":
        target_tokens = normalize_tokens(unit)
        if len(target_tokens) != 1:
            raise ValueError("word unit must normalize to exactly one lexical token")
        return target_tokens[0] in text_tokens

    if unit_type == "chunk":
        if FRAME_PLACEHOLDER in unit:
            raise ValueError("chunk unit must not contain a frame placeholder")
        target_tokens = normalize_tokens(unit)
        if len(target_tokens) < 2:
            raise ValueError("chunk unit must contain at least two lexical tokens")
        return _contains_chunk(text_tokens, target_tokens)

    before_tokens, after_tokens = _frame_parts(unit)
    return _contains_frame(text_tokens, before_tokens, after_tokens)
