"""Golden tests for shared canonical and strict artifact JSON infrastructure."""

from __future__ import annotations

import ast
import inspect
import re

import pytest

import vocab.context_batch as context_batch
import vocab.corpus as corpus
import vocab.forge.event_payloads as forge_event_payloads
import vocab.reconcile as reconcile
import vocab.tts as tts
from vocab.artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from vocab.models import VocabUnit


LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

UNICODE_VALUE = {"z": 1, "a": "tiếng Việt"}
UNICODE_BYTES = '{"a":"tiếng Việt","z":1}'.encode("utf-8")
UNICODE_SHA256 = "a0837b29becdee09e67a5afcb8ac29a31323271510e6714228768394c81c42ab"

T8_IDENTITY = {
    "definition_en": "not obvious",
    "lemma": "subtle",
    "lemma_slug": "subtle",
    "register": "neutral",
    "sense_slug": "small-difference",
    "source_ref": "dictionary:cambridge:subtle",
    "source_sentence": "The distinction is subtle.",
    "unit_key": "subtle::small-difference",
    "unit_type": "word",
}
T8_SHA256 = "17417e0ac43b4c2c522ff873ebe81f02a2ca7ece316414c110a119af45476419"

FORGE_IDENTITY = {
    "learner_note": "contrast the close meanings",
    "source_ref": "dictionary:cambridge:subtle",
    "source_sentence": "The difference between the two shades is subtle.",
}
FORGE_SHA256 = "405dd6e1941821c99d0a5a0c0c01bead24251b1622112ca1f77b5f60a3ffe1e3"

T9_INITIAL_IDENTITY = {
    "channel": "R",
    "unit_key": "subtle::small-difference",
}
T9_INITIAL_SHA256 = "16b29f6d467f4e903eb1e007b02775375e64f884ccf1d16651bd9015b5523d19"

T10_SNAPSHOT_IDENTITY = {
    "files": [
        {"path": "a.txt", "sha256": "0" * 64},
        {"path": "b.txt", "sha256": "f" * 64},
    ],
    "scan_version": 1,
}
T10_SNAPSHOT_SHA256 = (
    "0151c7b829df8b620740d8ac227537bb075e1d42c04e5e47fc113d396a1c4ca8"
)

T10_ENCOUNTER_IDENTITY = {
    "producer": "t10-corpus",
    "scan_version": 1,
    "source": "reading",
    "month": "2026-08",
    "unit_key": "art::creative-work",
}
T10_ENCOUNTER_SHA256 = (
    "5fcb9721e6cd7c3c75aea0b80bb7e34590356ac2ef2a4ea7923978ee8e6f2bb2"
)


def test_canonical_json_bytes_match_frozen_unicode_vector() -> None:
    assert canonical_json_bytes(UNICODE_VALUE) == UNICODE_BYTES
    assert b"\\u" not in UNICODE_BYTES
    assert b'", "' not in UNICODE_BYTES
    assert b'": ' not in UNICODE_BYTES


def test_canonical_sha256_matches_frozen_vector_and_is_deterministic() -> None:
    assert canonical_sha256(UNICODE_VALUE) == UNICODE_SHA256
    assert canonical_sha256(UNICODE_VALUE) == canonical_sha256(UNICODE_VALUE)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_bytes_reject_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"value": value})


def test_historical_exports_are_direct_shared_function_aliases() -> None:
    assert context_batch.canonical_json_bytes is canonical_json_bytes
    assert forge_event_payloads.canonical_json_bytes is canonical_json_bytes
    assert forge_event_payloads.canonical_sha256 is canonical_sha256
    assert reconcile._canonical_sha256 is canonical_sha256
    assert corpus._canonical_json_bytes is canonical_json_bytes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"[]", []),
        (b"1", 1),
        (b'"value"', "value"),
        (b"true", True),
        (b"null", None),
    ],
)
def test_strict_json_loads_allows_valid_arrays_and_scalars(
    raw: bytes,
    expected: object,
) -> None:
    assert strict_json_loads(raw) == expected


def test_strict_json_loads_rejects_nested_duplicate_keys() -> None:
    with pytest.raises(ArtifactJSONError):
        strict_json_loads(b'{"outer":{"key":1,"key":2}}')


@pytest.mark.parametrize("raw", [b"\xff", b"{", b"\xef\xbb\xbf{}"])
def test_strict_json_loads_rejects_invalid_transport(raw: bytes) -> None:
    with pytest.raises(ArtifactJSONError):
        strict_json_loads(raw)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_strict_json_loads_rejects_non_standard_constants(
    constant: bytes,
) -> None:
    with pytest.raises(ArtifactJSONError):
        strict_json_loads(b'{"value":' + constant + b"}")


@pytest.mark.parametrize("number", [b"1e999", b"-1e999"])
def test_strict_json_loads_rejects_float_overflow(number: bytes) -> None:
    with pytest.raises(ArtifactJSONError):
        strict_json_loads(b'{"value":' + number + b"}")


def test_strict_json_loads_preserves_finite_float_semantics() -> None:
    value = strict_json_loads(b'{"value":-12.5e2}')

    assert value == {"value": -1250.0}
    assert isinstance(value, dict)
    assert type(value["value"]) is float


@pytest.mark.parametrize("raw", ["{}", bytearray(b"{}"), memoryview(b"{}")])
def test_strict_json_loads_non_bytes_raise_type_error(raw: object) -> None:
    with pytest.raises(TypeError):
        strict_json_loads(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{",
        b'{"outer":{"key":1,"key":2}}',
        b'{"value":NaN}',
        b'{"value":1e999}',
    ],
)
def test_context_batch_translates_shared_transport_errors(raw: bytes) -> None:
    with pytest.raises(context_batch.ContextBatchTransportError):
        context_batch.parse_context_response(raw)


def test_context_batch_preserves_non_bytes_type_error() -> None:
    with pytest.raises(TypeError):
        context_batch.parse_context_response("{}")  # type: ignore[arg-type]


def test_t8_request_identity_matches_pre_refactor_golden() -> None:
    unit = VocabUnit(**T8_IDENTITY)

    assert canonical_json_bytes(T8_IDENTITY) == (
        b'{"definition_en":"not obvious","lemma":"subtle","lemma_slug":"subtle",'
        b'"register":"neutral","sense_slug":"small-difference",'
        b'"source_ref":"dictionary:cambridge:subtle",'
        b'"source_sentence":"The distinction is subtle.",'
        b'"unit_key":"subtle::small-difference","unit_type":"word"}'
    )
    assert context_batch.request_id_for_unit(unit) == T8_SHA256


def test_forge_identity_matches_pre_refactor_golden() -> None:
    assert canonical_json_bytes(FORGE_IDENTITY) == (
        b'{"learner_note":"contrast the close meanings",'
        b'"source_ref":"dictionary:cambridge:subtle",'
        b'"source_sentence":"The difference between the two shades is subtle."}'
    )
    assert forge_event_payloads.canonical_sha256(FORGE_IDENTITY) == FORGE_SHA256


def test_t9_initial_episode_identity_matches_pre_refactor_golden() -> None:
    assert canonical_json_bytes(T9_INITIAL_IDENTITY) == (
        b'{"channel":"R","unit_key":"subtle::small-difference"}'
    )
    assert reconcile._initial_new_episode_id(
        T9_INITIAL_IDENTITY["unit_key"],
        T9_INITIAL_IDENTITY["channel"],
    ) == f"initial-new:{T9_INITIAL_SHA256}"


def test_t10_snapshot_identity_matches_pre_refactor_golden() -> None:
    files = (
        corpus.CorpusFileSnapshot("a.txt", "0" * 64, ()),
        corpus.CorpusFileSnapshot("b.txt", "f" * 64, ()),
    )

    assert corpus._corpus_digest(files) == T10_SNAPSHOT_SHA256


def test_t10_encounter_identity_matches_pre_refactor_golden() -> None:
    assert corpus._encounter_id(
        unit_key=T10_ENCOUNTER_IDENTITY["unit_key"],
        producer=T10_ENCOUNTER_IDENTITY["producer"],
        scan_version=T10_ENCOUNTER_IDENTITY["scan_version"],
        source=T10_ENCOUNTER_IDENTITY["source"],
        month=T10_ENCOUNTER_IDENTITY["month"],
    ) == T10_ENCOUNTER_SHA256


@pytest.mark.parametrize(
    "digest",
    [
        UNICODE_SHA256,
        T8_SHA256,
        FORGE_SHA256,
        T9_INITIAL_SHA256,
        T10_SNAPSHOT_SHA256,
        T10_ENCOUNTER_SHA256,
    ],
)
def test_frozen_digests_are_full_lowercase_sha256(digest: str) -> None:
    assert LOWER_SHA256_RE.fullmatch(digest) is not None


def test_tts_does_not_import_shared_artifact_json() -> None:
    tree = ast.parse(inspect.getsource(tts))

    assert all(
        not (
            isinstance(node, ast.ImportFrom)
            and node.module == "artifact_json"
        )
        for node in ast.walk(tree)
    )
