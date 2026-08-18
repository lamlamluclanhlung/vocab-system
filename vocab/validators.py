"""Deterministic validation helpers for vocabulary units."""

from __future__ import annotations

import re
import unicodedata

from .contracts import (
    APOSTROPHE_EQUIVALENTS,
    CANONICAL_APOSTROPHE,
    CHANNELS,
    CHUNK_MAX_INSERTED_TOKENS,
    FORGE_VIOLATION_CODES,
    FRAME_MIN_FIXED_TOKENS,
    FRAME_PLACEHOLDER,
    FRAME_PLACEHOLDER_COUNT,
    FRAME_SLOT_MAX_TOKENS,
    FRAME_SLOT_MIN_TOKENS,
    LEXICAL_TOKEN_PATTERN,
    REGISTER_VALUES,
    SLUG_PATTERN,
    SOURCE_REF_PATTERN,
    STATES,
    STATE_FIELD_BY_CHANNEL,
    TARGET_FIELD_BY_CHANNEL,
    TARGET_FLAG_VALUE,
    TARGET_FLAG_VALUES,
    TEXT_NORMALIZATION_FORM,
    UNIT_KEY_PATTERN,
    UNIT_KEY_SEPARATOR,
    UNIT_TYPE_VALUES,
)
from .models import VocabUnit


_LEXICAL_TOKEN_RE = re.compile(LEXICAL_TOKEN_PATTERN)
_APOSTROPHE_TRANSLATION = str.maketrans(
    {apostrophe: CANONICAL_APOSTROPHE for apostrophe in APOSTROPHE_EQUIVALENTS}
)
_FRAME_PLACEHOLDER_TOKEN_RE = re.compile(
    rf"(?<![\w']){re.escape(FRAME_PLACEHOLDER)}(?![\w'])"
)
_SLUG_RE = re.compile(SLUG_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SOURCE_REF_RE = re.compile(SOURCE_REF_PATTERN)


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


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    """Return whether *value* is a string fully matching *pattern*."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_nonempty_text(value: object) -> bool:
    """Return whether *value* is a string containing non-whitespace text."""
    return isinstance(value, str) and bool(value.strip())


def _has_valid_unit_shape(lemma: str, unit_type: str) -> bool:
    """Validate D19 shape by exercising the shared Unit matcher."""
    try:
        contains_unit("", lemma, unit_type)
    except ValueError:
        return False
    return True


def validate_forge_unit(unit: VocabUnit) -> tuple[str, ...]:
    """Return ordered deterministic Forge-stage violations for *unit*."""
    discovered: list[str] = []

    if not _matches(_SLUG_RE, unit.lemma_slug):
        discovered.append("F_LEMMA_SLUG_INVALID")
    if not _matches(_SLUG_RE, unit.sense_slug):
        discovered.append("F_SENSE_SLUG_INVALID")
    if not _matches(_UNIT_KEY_RE, unit.unit_key):
        discovered.append("F_UNIT_KEY_INVALID")
    if isinstance(unit.lemma_slug, str) and isinstance(unit.sense_slug, str):
        expected_unit_key = (
            unit.lemma_slug + UNIT_KEY_SEPARATOR + unit.sense_slug
        )
        if unit.unit_key != expected_unit_key:
            discovered.append("F_UNIT_KEY_MISMATCH")

    lemma_valid = _is_nonempty_text(unit.lemma)
    unit_type_valid = unit.unit_type in UNIT_TYPE_VALUES
    if not lemma_valid:
        discovered.append("F_LEMMA_EMPTY")
    if not unit_type_valid:
        discovered.append("F_UNIT_TYPE_INVALID")

    unit_shape_valid = False
    if lemma_valid and unit_type_valid:
        unit_shape_valid = _has_valid_unit_shape(unit.lemma, unit.unit_type)
        if not unit_shape_valid:
            discovered.append("F_UNIT_SHAPE_INVALID")

    target_values: dict[str, object] = {}
    target_validity: dict[str, bool] = {}
    for channel in CHANNELS:
        target_value = getattr(unit, TARGET_FIELD_BY_CHANNEL[channel])
        target_values[channel] = target_value
        target_validity[channel] = target_value in TARGET_FLAG_VALUES
        if not target_validity[channel]:
            discovered.append(f"F_TARGET_{channel}_INVALID")

    if all(target_validity.values()) and not any(
        value == TARGET_FLAG_VALUE for value in target_values.values()
    ):
        discovered.append("F_NO_TARGET_ENABLED")

    state_values: dict[str, object] = {}
    state_validity: dict[str, bool] = {}
    for channel in CHANNELS:
        state_value = getattr(unit, STATE_FIELD_BY_CHANNEL[channel])
        state_values[channel] = state_value
        state_validity[channel] = state_value == "" or state_value in STATES
        if not state_validity[channel]:
            discovered.append(f"F_STATE_{channel}_INVALID")

    for channel in CHANNELS:
        if not (target_validity[channel] and state_validity[channel]):
            continue
        target_enabled = target_values[channel] == TARGET_FLAG_VALUE
        state_present = state_values[channel] != ""
        if target_enabled != state_present:
            discovered.append(f"F_TARGET_STATE_{channel}_MISMATCH")

    if unit.register not in REGISTER_VALUES:
        discovered.append("F_REGISTER_INVALID")
    if not _is_nonempty_text(unit.definition_en):
        discovered.append("F_DEFINITION_EMPTY")
    if not _matches(_SOURCE_REF_RE, unit.source_ref):
        discovered.append("F_SOURCE_REF_INVALID")

    source_sentence_valid = _is_nonempty_text(unit.source_sentence)
    if not source_sentence_valid:
        discovered.append("F_SOURCE_SENTENCE_EMPTY")
    elif lemma_valid and unit_type_valid and unit_shape_valid:
        if not contains_unit(unit.source_sentence, unit.lemma, unit.unit_type):
            discovered.append("F_SOURCE_UNIT_MISSING")

    return tuple(
        code for code in FORGE_VIOLATION_CODES if code in discovered
    )
