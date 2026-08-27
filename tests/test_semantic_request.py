"""Deterministic tests for the closed D59 T11 semantic-request artifact."""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import re
from collections.abc import Mapping

import pytest

import vocab.semantic_request as semantic_request_module
from vocab.semantic_request import (
    SEMANTIC_PROMPT_ID,
    SEMANTIC_PROMPT_TEXT_V1,
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_PROTOCOL_ID,
    SEMANTIC_PROTOCOL_TEXT_V1,
    SEMANTIC_PROTOCOL_VERSION,
    SEMANTIC_REQUEST_ARTIFACT,
    SEMANTIC_REQUEST_VERSION,
    SEMANTIC_RUBRIC_ID,
    SEMANTIC_RUBRIC_TEXT_V1,
    SEMANTIC_RUBRIC_VERSION,
    SemanticRequestError,
    build_semantic_request,
    import_semantic_request,
    prepare_semantic_request_submission,
    semantic_request_digest,
    serialize_semantic_request,
)


LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TASK_CONTENT_BY_CHANNEL = {
    "R": {
        "passage": "Sự khác biệt giữa hai phương án rất subtle.",
        "question": "What does subtle mean in this passage?",
        "learner_response": (
            "It means the difference is slight and not immediately obvious."
        ),
    },
    "L": {
        "spoken_script": "The distinction between the proposals was subtle.",
        "question": "How did the speaker describe the distinction?",
        "learner_response": "The distinction was slight and hard to notice.",
    },
    "W": {
        "production_prompt": "Compare two similar research results.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "learner_response": "The studies showed a subtle difference in timing.",
    },
    "S": {
        "production_prompt": "Describe a small difference between two plans.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "approved_transcript": "There is a subtle difference in their priorities.",
    },
}

TASK_KIND_BY_CHANNEL = {
    "R": "reading_comprehension",
    "L": "listening_comprehension",
    "W": "written_production",
    "S": "spoken_production",
}

TASK_KEYS_BY_CHANNEL = {
    channel: {"channel", "task_kind", *content}
    for channel, content in TASK_CONTENT_BY_CHANNEL.items()
}

GOLDEN_R_REQUEST = {
    "artifact": "vocab.t11.semantic-request",
    "v": 1,
    "protocol": {
        "id": "t11-semantic-assessment",
        "version": 1,
        "text": SEMANTIC_PROTOCOL_TEXT_V1,
    },
    "rubric": {
        "id": "d52-d53-lexical-assessment",
        "version": 1,
        "text": SEMANTIC_RUBRIC_TEXT_V1,
    },
    "prompt": {
        "id": "t11-semantic-bridge",
        "version": 1,
        "text": SEMANTIC_PROMPT_TEXT_V1,
    },
    "unit": {
        "unit_key": "subtle::small-difference",
        "lemma": "subtle",
        "unit_type": "word",
        "definition_en": "not immediately obvious; tinh tế",
    },
    "task": {
        "channel": "R",
        "task_kind": "reading_comprehension",
        **TASK_CONTENT_BY_CHANNEL["R"],
    },
}

GOLDEN_R_CANONICAL_BYTES = json.dumps(
    GOLDEN_R_REQUEST,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
GOLDEN_R_REQUEST_DIGEST = (
    "7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e"
)

TEXT_SHA256 = {
    "protocol": "a90c45b2367fbd9a107ee034e812eb9f0c8396d5695a2c77a3b6bd9fae6b1b95",
    "rubric": "914d7e66b402991a5dfb587f956565273d0fdf57110c35432543ba3a55156d6d",
    "prompt": "5fb2ea7114b824276c645f0a7484957f7494331c74d75784855f58b020420007",
}


def make_request(channel: str = "R", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "unit_key": "subtle::small-difference",
        "lemma": "subtle",
        "unit_type": "word",
        "definition_en": "not immediately obvious; tinh tế",
        "channel": channel,
        "task_content": TASK_CONTENT_BY_CHANNEL[channel],
    }
    values.update(overrides)
    return build_semantic_request(**values)  # type: ignore[arg-type]


def transport_bytes(value: object, **kwargs: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, **kwargs).encode("utf-8")


def set_path(value: dict[str, object], path: tuple[str, ...], replacement: object) -> None:
    target: dict[str, object] = value
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = replacement


def remove_path(value: dict[str, object], path: tuple[str, ...]) -> None:
    target: dict[str, object] = value
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    del target[path[-1]]


def test_frozen_labels_versions_and_texts_are_exact() -> None:
    assert (
        SEMANTIC_REQUEST_ARTIFACT,
        SEMANTIC_REQUEST_VERSION,
        SEMANTIC_PROTOCOL_ID,
        SEMANTIC_PROTOCOL_VERSION,
        SEMANTIC_RUBRIC_ID,
        SEMANTIC_RUBRIC_VERSION,
        SEMANTIC_PROMPT_ID,
        SEMANTIC_PROMPT_VERSION,
    ) == (
        "vocab.t11.semantic-request",
        1,
        "t11-semantic-assessment",
        1,
        "d52-d53-lexical-assessment",
        1,
        "t11-semantic-bridge",
        1,
    )
    assert hashlib.sha256(SEMANTIC_PROTOCOL_TEXT_V1.encode()).hexdigest() == (
        TEXT_SHA256["protocol"]
    )
    assert hashlib.sha256(SEMANTIC_RUBRIC_TEXT_V1.encode()).hexdigest() == (
        TEXT_SHA256["rubric"]
    )
    assert hashlib.sha256(SEMANTIC_PROMPT_TEXT_V1.encode()).hexdigest() == (
        TEXT_SHA256["prompt"]
    )


@pytest.mark.parametrize("channel", ["R", "L", "W", "S"])
def test_builder_emits_exact_closed_schema_for_each_channel(channel: str) -> None:
    request = make_request(channel)

    assert set(request) == {
        "artifact",
        "v",
        "protocol",
        "rubric",
        "prompt",
        "unit",
        "task",
    }
    assert set(request["protocol"]) == {"id", "version", "text"}
    assert set(request["rubric"]) == {"id", "version", "text"}
    assert set(request["prompt"]) == {"id", "version", "text"}
    assert set(request["unit"]) == {
        "unit_key",
        "lemma",
        "unit_type",
        "definition_en",
    }
    task = request["task"]
    assert isinstance(task, dict)
    assert set(task) == TASK_KEYS_BY_CHANNEL[channel]
    assert task["channel"] == channel
    assert task["task_kind"] == TASK_KIND_BY_CHANNEL[channel]


@pytest.mark.parametrize("channel", ["R", "L", "W", "S"])
def test_each_channel_constructs_serializes_imports_and_digests(channel: str) -> None:
    request = make_request(channel)
    raw = serialize_semantic_request(request)
    digest = semantic_request_digest(request)

    assert import_semantic_request(raw) == request
    assert serialize_semantic_request(import_semantic_request(raw)) == raw
    assert LOWER_SHA256_RE.fullmatch(digest) is not None


def test_complete_reading_golden_bytes_and_digest_are_frozen() -> None:
    request = make_request("R")

    assert request == GOLDEN_R_REQUEST
    assert len(GOLDEN_R_CANONICAL_BYTES) == 11130
    assert hashlib.sha256(GOLDEN_R_CANONICAL_BYTES).hexdigest() == (
        GOLDEN_R_REQUEST_DIGEST
    )
    assert serialize_semantic_request(request) == GOLDEN_R_CANONICAL_BYTES
    assert semantic_request_digest(request) == GOLDEN_R_REQUEST_DIGEST


def test_transport_order_and_insignificant_whitespace_do_not_change_identity() -> None:
    request = make_request("R")
    reordered = {
        key: request[key]
        for key in reversed(tuple(request))
    }
    pretty = transport_bytes(reordered, indent=2)
    compact_unsorted = transport_bytes(reordered, separators=(",", ":"))

    first = import_semantic_request(pretty)
    second = import_semantic_request(compact_unsorted)
    assert serialize_semantic_request(first) == serialize_semantic_request(second)
    assert semantic_request_digest(first) == semantic_request_digest(second)


def test_internal_string_whitespace_is_preserved_and_changes_identity() -> None:
    baseline = make_request("R")
    changed = copy.deepcopy(baseline)
    task = changed["task"]
    assert isinstance(task, dict)
    task["learner_response"] = (
        "It means the difference is  slight and not immediately obvious."
    )

    imported = import_semantic_request(transport_bytes(changed))
    assert imported["task"] == task
    assert semantic_request_digest(imported) != semantic_request_digest(baseline)


def test_unicode_content_is_preserved_without_ascii_escaping() -> None:
    request = make_request("R")
    raw = serialize_semantic_request(request)
    imported = import_semantic_request(raw)

    assert "Sự khác biệt".encode("utf-8") in raw
    assert "tinh tế".encode("utf-8") in raw
    assert imported == request


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("v",), True),
        (("v",), 1.0),
        (("protocol", "version"), True),
        (("protocol", "version"), 1.0),
        (("rubric", "version"), True),
        (("rubric", "version"), 1.0),
        (("prompt", "version"), True),
        (("prompt", "version"), 1.0),
    ],
)
def test_integer_traps_are_rejected(
    path: tuple[str, ...],
    replacement: object,
) -> None:
    request = make_request("R")
    set_path(request, path, replacement)

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


@pytest.mark.parametrize("scope", ["top", "protocol", "rubric", "prompt", "unit", "task"])
def test_unknown_keys_are_rejected_at_every_object_level(scope: str) -> None:
    request = make_request("R")
    target = request if scope == "top" else request[scope]
    assert isinstance(target, dict)
    target["extra"] = "forbidden"

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


@pytest.mark.parametrize(
    "path",
    [
        ("artifact",),
        ("protocol", "id"),
        ("rubric", "text"),
        ("prompt", "version"),
        ("unit", "lemma"),
        ("task", "question"),
    ],
)
def test_missing_keys_are_rejected_at_every_object_level(
    path: tuple[str, ...],
) -> None:
    request = make_request("R")
    remove_path(request, path)

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


NULL_PATHS = [
    ("artifact",),
    ("v",),
    ("protocol",),
    ("rubric",),
    ("prompt",),
    ("unit",),
    ("task",),
    *(("protocol", field) for field in ("id", "version", "text")),
    *(("rubric", field) for field in ("id", "version", "text")),
    *(("prompt", field) for field in ("id", "version", "text")),
    *(("unit", field) for field in ("unit_key", "lemma", "unit_type", "definition_en")),
    *(("task", field) for field in TASK_KEYS_BY_CHANNEL["R"]),
]


@pytest.mark.parametrize("path", NULL_PATHS)
def test_null_is_rejected_for_every_required_field(path: tuple[str, ...]) -> None:
    request = make_request("R")
    set_path(request, path, None)

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


def test_channel_task_kind_mismatch_is_rejected_without_repair() -> None:
    request = make_request("R")
    task = request["task"]
    assert isinstance(task, dict)
    task["task_kind"] = "spoken_production"

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)
    assert task["task_kind"] == "spoken_production"


@pytest.mark.parametrize(
    ("channel", "removed", "added"),
    [
        ("W", "learner_response", "approved_transcript"),
        ("S", "approved_transcript", "learner_response"),
    ],
)
def test_productive_channel_keysets_are_not_interchangeable(
    channel: str,
    removed: str,
    added: str,
) -> None:
    request = make_request(channel)
    task = request["task"]
    assert isinstance(task, dict)
    value = task.pop(removed)
    task[added] = value

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


@pytest.mark.parametrize(
    "path",
    [
        ("protocol", "text"),
        ("rubric", "text"),
        ("prompt", "text"),
        ("unit", "lemma"),
        ("unit", "definition_en"),
        ("task", "passage"),
        ("task", "question"),
        ("task", "learner_response"),
    ],
)
@pytest.mark.parametrize("bad_text", ["", "   "])
def test_empty_and_whitespace_only_required_strings_are_rejected(
    path: tuple[str, ...],
    bad_text: str,
) -> None:
    request = make_request("R")
    set_path(request, path, bad_text)

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


STRING_PATHS = [
    ("artifact",),
    *((scope, field) for scope in ("protocol", "rubric", "prompt") for field in ("id", "text")),
    *(("unit", field) for field in ("unit_key", "lemma", "unit_type", "definition_en")),
    *(("task", field) for field in TASK_KEYS_BY_CHANNEL["R"] if field != "task_kind"),
    ("task", "task_kind"),
]


@pytest.mark.parametrize("path", STRING_PATHS)
def test_unpaired_surrogate_is_rejected_in_every_string_position(
    path: tuple[str, ...],
) -> None:
    request = make_request("R")
    set_path(request, path, "\ud800")

    with pytest.raises(SemanticRequestError):
        serialize_semantic_request(request)


def test_accepted_strings_are_not_trimmed_or_normalized() -> None:
    request = make_request(
        "R",
        lemma="  subtle  ",
        definition_en="  not obvious  ",
        protocol_text="  protocol text  ",
        task_content={
            **TASK_CONTENT_BY_CHANNEL["R"],
            "question": "  What does it mean?  ",
        },
    )
    imported = import_semantic_request(serialize_semantic_request(request))

    assert imported["protocol"]["text"] == "  protocol text  "
    assert imported["unit"]["lemma"] == "  subtle  "
    assert imported["unit"]["definition_en"] == "  not obvious  "
    assert imported["task"]["question"] == "  What does it mean?  "


def test_zero_width_format_character_is_not_newly_prohibited() -> None:
    request = make_request("R", lemma="\u200b")

    assert import_semantic_request(serialize_semantic_request(request)) == request


@pytest.mark.parametrize(
    "path",
    [
        ("protocol", "text"),
        ("rubric", "text"),
        ("prompt", "text"),
        ("unit", "definition_en"),
        ("task", "passage"),
        ("task", "learner_response"),
    ],
)
def test_every_mutable_request_content_field_is_digest_significant(
    path: tuple[str, ...],
) -> None:
    baseline = make_request("R")
    changed = copy.deepcopy(baseline)
    target: Mapping[str, object] = changed
    for key in path:
        value = target[key]
        if key == path[-1]:
            assert isinstance(value, str)
            set_path(changed, path, value + "!")
            break
        assert isinstance(value, Mapping)
        target = value

    assert semantic_request_digest(changed) != semantic_request_digest(baseline)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("artifact",), "vocab.t11.semantic-response"),
        (("v",), 2),
    ],
)
def test_invalid_discriminator_and_version_reject_before_identity_use(
    path: tuple[str, ...],
    replacement: object,
) -> None:
    request = make_request("R")
    set_path(request, path, replacement)

    with pytest.raises(SemanticRequestError):
        semantic_request_digest(request)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{",
        b'{"outer":{"key":1,"key":2}}',
        b'{"value":NaN}',
        b'{"value":1e999}',
    ],
)
def test_strict_import_rejects_invalid_transport(raw: bytes) -> None:
    with pytest.raises(SemanticRequestError):
        import_semantic_request(raw)


@pytest.mark.parametrize("raw", [b"[]", b"1", b'"request"', b"null"])
def test_strict_import_rejects_top_level_array_or_scalar(raw: bytes) -> None:
    with pytest.raises(SemanticRequestError):
        import_semantic_request(raw)


def test_strict_import_non_bytes_remains_type_error() -> None:
    with pytest.raises(TypeError):
        import_semantic_request("{}")  # type: ignore[arg-type]


def test_submission_helper_returns_only_canonical_bytes_and_external_digest() -> None:
    request = make_request("R")

    prepared = prepare_semantic_request_submission(request)

    assert type(prepared) is tuple
    assert prepared == (
        serialize_semantic_request(request),
        semantic_request_digest(request),
    )
    request_value = json.loads(prepared[0])
    assert "request_digest" not in request_value
    assert prepared[1] == GOLDEN_R_REQUEST_DIGEST


PROHIBITED_REQUEST_KEYS = {
    "request_digest",
    "session_id",
    "item_ordinal",
    "attempt_id",
    "assessment_id",
    "stimulus_ref",
    "presented_stimulus_ref",
    "stimulus_artifact_ref",
    "response_artifact_ref",
    "response_audio_ref",
    "novel",
    "producer",
    "producer_version",
    "reserved_at",
    "ts",
    "day",
    "model_id",
    "model_version",
    "authority_kind",
    "raw_audio",
    "audio_path",
    "stt_candidate",
    "stt_confidence",
    "transcription_provenance",
}


def test_request_representation_contains_no_t12_eventlog_or_speech_owned_keys() -> None:
    request = make_request("S")

    discovered: set[str] = set()

    def collect_keys(value: object) -> None:
        if isinstance(value, dict):
            discovered.update(value)
            for nested in value.values():
                collect_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_keys(nested)

    collect_keys(request)
    assert discovered.isdisjoint(PROHIBITED_REQUEST_KEYS)
    assert set(request["task"]) == TASK_KEYS_BY_CHANNEL["S"]


def test_semantic_request_module_has_no_io_eventlog_or_anki_imports() -> None:
    tree = ast.parse(inspect.getsource(semantic_request_module))
    prohibited = {"os", "pathlib", "events", "anki"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[-1])

    assert imported.isdisjoint(prohibited)
