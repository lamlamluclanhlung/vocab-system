"""Freeze machine-checkable T10 corpus contracts without runtime scanning."""

from __future__ import annotations

import re

import vocab.contracts as contracts
from vocab.contracts import (
    APOSTROPHE_EQUIVALENTS,
    CANONICAL_APOSTROPHE,
    CHUNK_MAX_INSERTED_TOKENS,
    CORPUS_ALLOW_SYMLINKS,
    CORPUS_ALLOW_UTF8_BOM,
    CORPUS_BLANK_LINE_IS_BLOCK_BOUNDARY,
    CORPUS_DIRECT_CHILDREN_ONLY,
    CORPUS_EXTENSIONS,
    CORPUS_MONTH_PATTERN,
    CORPUS_RAW_BYTES_DEFINE_FILE_IDENTITY,
    CORPUS_REJECT_URL_PREFIXES,
    CORPUS_SCAN_VERSION,
    CORPUS_SENTENCE_TERMINATORS,
    CORPUS_SINGLE_NEWLINE_IS_BLOCK_BOUNDARY,
    CORPUS_SOURCE_PATTERN,
    EVENT_PAYLOAD_REQUIRED_FIELDS,
    EVENT_SCHEMA_VERSION,
    FRAME_SLOT_MAX_TOKENS,
    FRAME_SLOT_MIN_TOKENS,
    LEXICAL_TOKEN_PATTERN,
    SLUG_PATTERN,
    T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS,
    T10_ENCOUNTER_EMIT_ZERO_COUNTS,
    T10_ENCOUNTER_PRODUCER_ID,
    T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS,
    TEXT_NORMALIZATION_FORM,
    UNIT_TYPE_VALUES,
)


def test_corpus_scan_version_is_frozen() -> None:
    assert CORPUS_SCAN_VERSION == 1


def test_corpus_month_pattern_accepts_only_real_zero_padded_months() -> None:
    for valid in ("2026-01", "2026-12"):
        assert re.fullmatch(CORPUS_MONTH_PATTERN, valid) is not None
    for invalid in ("2026-00", "2026-13", "26-01", "2026-1"):
        assert re.fullmatch(CORPUS_MONTH_PATTERN, invalid) is None


def test_corpus_source_pattern_reuses_one_anchored_slug() -> None:
    assert CORPUS_SOURCE_PATTERN == rf"^{SLUG_PATTERN}$"
    for valid in ("reading", "own-writing", "source2"):
        assert re.fullmatch(CORPUS_SOURCE_PATTERN, valid) is not None
    for invalid in (
        "Own-Writing",
        "own writing",
        "-reading",
        "reading-",
        "reading--notes",
    ):
        assert re.fullmatch(CORPUS_SOURCE_PATTERN, invalid) is None


def test_corpus_extensions_are_plaintext_only() -> None:
    assert CORPUS_EXTENSIONS == (".txt",)


def test_corpus_snapshot_file_policies_are_exact() -> None:
    assert CORPUS_DIRECT_CHILDREN_ONLY is True
    assert CORPUS_ALLOW_SYMLINKS is False
    assert CORPUS_ALLOW_UTF8_BOM is True
    assert CORPUS_RAW_BYTES_DEFINE_FILE_IDENTITY is True


def test_corpus_block_boundary_contract_is_exact() -> None:
    assert CORPUS_SENTENCE_TERMINATORS == (".", "!", "?", "…")
    assert CORPUS_BLANK_LINE_IS_BLOCK_BOUNDARY is True
    assert CORPUS_SINGLE_NEWLINE_IS_BLOCK_BOUNDARY is False


def test_corpus_url_rejection_prefixes_are_exact() -> None:
    assert CORPUS_REJECT_URL_PREFIXES == ("http://", "https://", "www.")


def test_t10_encounter_producer_contract_is_exact() -> None:
    assert T10_ENCOUNTER_PRODUCER_ID == "t10-corpus"
    assert T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS == (
        "count",
        "source",
        "month",
        "producer",
        "scan_version",
        "encounter_id",
        "lemma",
        "unit_type",
        "corpus_snapshot_digest",
        "corpus_file_count",
    )
    assert T10_ENCOUNTER_EMIT_ZERO_COUNTS is True


def test_t10_encounter_allowed_payload_fields_are_the_required_tuple() -> None:
    assert (
        T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS
        == T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS
    )
    assert (
        T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS
        is T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS
    )
    assert all(
        isinstance(field, str) for field in T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS
    )


def test_generic_encounter_contract_and_schema_version_are_unchanged() -> None:
    assert EVENT_PAYLOAD_REQUIRED_FIELDS["ENCOUNTER"] == (
        "count",
        "source",
        "month",
    )
    assert EVENT_SCHEMA_VERSION == 1


def test_t10_reuses_existing_unit_type_vocabulary() -> None:
    assert UNIT_TYPE_VALUES == ("word", "chunk", "frame")
    assert "unit_type" in T10_ENCOUNTER_REQUIRED_PAYLOAD_FIELDS
    assert not hasattr(contracts, "T10_UNIT_TYPE_VALUES")


def test_d19_normalization_and_matching_constants_remain_frozen() -> None:
    assert TEXT_NORMALIZATION_FORM == "NFKC"
    assert APOSTROPHE_EQUIVALENTS == ("‘", "’", "ʼ", "＇")
    assert CANONICAL_APOSTROPHE == "'"
    assert LEXICAL_TOKEN_PATTERN == r"[^\W_]+(?:'[^\W_]+)*"
    assert CHUNK_MAX_INSERTED_TOKENS == 2
    assert FRAME_SLOT_MIN_TOKENS == 1
    assert FRAME_SLOT_MAX_TOKENS == 6
