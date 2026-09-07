"""Batch orchestration around the frozen one-Unit Forge bridge.

This module does not call a model and does not modify the Forge core. It only
packages multiple standard Forge request artifacts into one convenience
transport, then strict-validates and binds a matching batch response back to the
existing one-Unit bridge before any item is replayed through forge().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from ..forge.request import ForgeRequest
from . import forge_bridge
from .errors import RuntimeForgeBridgeError


BATCH_REQUEST_ARTIFACT = "vocab.forge.batch.request"
BATCH_RESPONSE_ARTIFACT = "vocab.forge.batch.response"
BATCH_ARTIFACT_VERSION = 1
BATCH_MAX_ITEMS = 20

BATCH_REQUEST_KEYS = frozenset(
    {"artifact", "artifact_version", "batch_id", "instructions", "items"}
)
BATCH_RESPONSE_KEYS = frozenset(
    {
        "artifact",
        "artifact_version",
        "source_batch_id",
        "model_id",
        "model_version",
        "items",
    }
)
BATCH_RESPONSE_ITEM_KEYS = frozenset(
    {"generation_request_sha256", "structured_output"}
)

BATCH_INSTRUCTIONS = """Each item is one complete vocab.forge.request artifact.
For every item, follow that item's prompt_text and json_schema and generate
exactly one structured_output for the intended Target Unit named in learner_note.
Preserve item order and generation_request_sha256 exactly. Do not merge items.
Return no prose outside one JSON object with exactly this shape:
{"artifact":"vocab.forge.batch.response","artifact_version":1,"source_batch_id":"<exact batch_id>","model_id":"<explicit model id>","model_version":"<explicit model version>","items":[{"generation_request_sha256":"<exact item hash>","structured_output":{...}}]}"""


@dataclass(frozen=True, slots=True)
class BatchRequestArtifact:
    batch_id: str
    requests: tuple[forge_bridge.ForgeRequestArtifact, ...]


@dataclass(frozen=True, slots=True)
class BatchResponseArtifact:
    source_batch_id: str
    responses: tuple[forge_bridge.ForgeResponseArtifact, ...]


def _closed_object(
    raw: object, expected_keys: frozenset[str], label: str
) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise RuntimeForgeBridgeError(f"{label} must be a JSON object")
    present = set(raw)
    unknown = sorted(present - expected_keys)
    if unknown:
        raise RuntimeForgeBridgeError(f"{label} has unknown keys: {unknown}")
    missing = sorted(expected_keys - present)
    if missing:
        raise RuntimeForgeBridgeError(f"{label} is missing keys: {missing}")
    return dict(raw)


def _required_sha256(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise RuntimeForgeBridgeError(
            f"{label} must be 64 lowercase hexadecimal characters"
        )
    if any(character not in "0123456789abcdef" for character in value):
        raise RuntimeForgeBridgeError(
            f"{label} must be 64 lowercase hexadecimal characters"
        )
    return value


def _required_clean_text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise RuntimeForgeBridgeError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise RuntimeForgeBridgeError(
            f"{label} must not have leading or trailing whitespace"
        )
    return value


def _batch_id_from_hashes(hashes: Sequence[str]) -> str:
    return canonical_sha256(
        [
            {"generation_request_sha256": request_hash}
            for request_hash in hashes
        ]
    )


def _batch_id_from_requests(
    requests: Sequence[forge_bridge.ForgeRequestArtifact],
) -> str:
    return _batch_id_from_hashes(
        [request.generation_request_sha256 for request in requests]
    )


def _learner_note(target: str, note: str) -> str:
    base = f"Target Unit: {target}"
    if note:
        return f"{base}\nLearner note: {note}"
    return base


def parse_batch_tsv(raw: bytes) -> tuple[ForgeRequest, ...]:
    """Parse 1-20 strict TSV rows into ordinary ForgeRequest values.

    Each non-empty row is:
        target<TAB>source_ref<TAB>source_sentence[<TAB>learner_note]

    source_ref is always supplied by the human. This helper never invents a
    source reference, slug, sense, definition, model identity, or target state.
    """
    if not isinstance(raw, bytes):
        raise TypeError("batch TSV body must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeForgeBridgeError("batch TSV must be valid UTF-8") from None
    if text.startswith("\ufeff"):
        raise RuntimeForgeBridgeError("batch TSV must not contain a UTF-8 BOM")

    requests: list[ForgeRequest] = []
    seen_hashes: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line == "":
            continue
        fields = line.split("\t")
        if len(fields) not in (3, 4):
            raise RuntimeForgeBridgeError(
                f"batch TSV line {line_number} must have exactly 3 or 4 tab-separated fields"
            )
        target = _required_clean_text(fields[0], f"batch TSV line {line_number} target")
        source_ref = _required_clean_text(
            fields[1], f"batch TSV line {line_number} source_ref"
        )
        source_sentence = _required_clean_text(
            fields[2], f"batch TSV line {line_number} source_sentence"
        )
        note = ""
        if len(fields) == 4 and fields[3] != "":
            note = _required_clean_text(
                fields[3], f"batch TSV line {line_number} learner_note"
            )

        request = ForgeRequest(
            source_ref=source_ref,
            source_sentence=source_sentence,
            learner_note=_learner_note(target, note),
        )
        request_hash = forge_bridge.generation_request_sha256(request)
        if request_hash in seen_hashes:
            raise RuntimeForgeBridgeError(
                f"batch TSV line {line_number} duplicates an earlier Forge request"
            )
        seen_hashes.add(request_hash)
        requests.append(request)
        if len(requests) > BATCH_MAX_ITEMS:
            raise RuntimeForgeBridgeError(
                f"batch TSV may contain at most {BATCH_MAX_ITEMS} items"
            )

    if not requests:
        raise RuntimeForgeBridgeError("batch TSV must contain at least one item")
    return tuple(requests)


def build_batch_request_artifact(
    tsv_raw: bytes,
    prompt: forge_bridge.ForgePrompt,
) -> bytes:
    """Package TSV input as one canonical batch of standard Forge requests."""
    requests = parse_batch_tsv(tsv_raw)
    request_bodies: list[Mapping[str, object]] = []
    parsed_requests: list[forge_bridge.ForgeRequestArtifact] = []

    for request in requests:
        standard_raw = forge_bridge.build_request_artifact(request, prompt)
        try:
            decoded = strict_json_loads(standard_raw)
        except (ArtifactJSONError, TypeError) as exc:
            raise RuntimeForgeBridgeError(
                f"generated Forge request artifact is not strict JSON: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise RuntimeForgeBridgeError(
                "generated Forge request artifact must be an object"
            )
        request_bodies.append(decoded)
        parsed_requests.append(forge_bridge.parse_request_artifact(standard_raw))

    batch_id = _batch_id_from_requests(parsed_requests)
    body = {
        "artifact": BATCH_REQUEST_ARTIFACT,
        "artifact_version": BATCH_ARTIFACT_VERSION,
        "batch_id": batch_id,
        "instructions": BATCH_INSTRUCTIONS,
        "items": request_bodies,
    }
    try:
        return canonical_json_bytes(body)
    except ArtifactJSONError as exc:
        raise RuntimeForgeBridgeError(
            f"batch request artifact is not canonical JSON: {exc}"
        ) from exc


def parse_batch_request_artifact(raw: bytes) -> BatchRequestArtifact:
    """Strict-decode a batch request and revalidate every standard item."""
    label = "Forge batch request artifact"
    try:
        decoded = strict_json_loads(raw)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeForgeBridgeError(f"{label} is not strict JSON: {exc}") from exc
    body = _closed_object(decoded, BATCH_REQUEST_KEYS, label)

    if body["artifact"] != BATCH_REQUEST_ARTIFACT:
        raise RuntimeForgeBridgeError(
            f"{label}.artifact must be {BATCH_REQUEST_ARTIFACT!r}"
        )
    if type(body["artifact_version"]) is not int or body["artifact_version"] != 1:
        raise RuntimeForgeBridgeError(f"{label}.artifact_version must be exactly 1")
    if body["instructions"] != BATCH_INSTRUCTIONS:
        raise RuntimeForgeBridgeError(f"{label}.instructions was edited")
    recorded_batch_id = _required_sha256(body["batch_id"], f"{label}.batch_id")

    raw_items = body["items"]
    if not isinstance(raw_items, list):
        raise RuntimeForgeBridgeError(f"{label}.items must be an array")
    if not 1 <= len(raw_items) <= BATCH_MAX_ITEMS:
        raise RuntimeForgeBridgeError(
            f"{label}.items must contain 1-{BATCH_MAX_ITEMS} items"
        )

    requests: list[forge_bridge.ForgeRequestArtifact] = []
    seen_hashes: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise RuntimeForgeBridgeError(f"{label}.items[{index}] must be an object")
        try:
            standard_raw = canonical_json_bytes(raw_item)
        except ArtifactJSONError as exc:
            raise RuntimeForgeBridgeError(
                f"{label}.items[{index}] is not canonical JSON: {exc}"
            ) from exc
        request = forge_bridge.parse_request_artifact(standard_raw)
        if request.generation_request_sha256 in seen_hashes:
            raise RuntimeForgeBridgeError(f"{label} contains a duplicate request hash")
        seen_hashes.add(request.generation_request_sha256)
        requests.append(request)

    expected_batch_id = _batch_id_from_requests(requests)
    if recorded_batch_id != expected_batch_id:
        raise RuntimeForgeBridgeError(f"{label}.batch_id does not match its items")
    return BatchRequestArtifact(recorded_batch_id, tuple(requests))


def parse_batch_response_artifact(raw: bytes) -> BatchResponseArtifact:
    """Strict-decode a batch response into ordinary Forge response artifacts."""
    label = "Forge batch response artifact"
    try:
        decoded = strict_json_loads(raw)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeForgeBridgeError(f"{label} is not strict JSON: {exc}") from exc
    body = _closed_object(decoded, BATCH_RESPONSE_KEYS, label)

    if body["artifact"] != BATCH_RESPONSE_ARTIFACT:
        raise RuntimeForgeBridgeError(
            f"{label}.artifact must be {BATCH_RESPONSE_ARTIFACT!r}"
        )
    if type(body["artifact_version"]) is not int or body["artifact_version"] != 1:
        raise RuntimeForgeBridgeError(f"{label}.artifact_version must be exactly 1")
    source_batch_id = _required_sha256(
        body["source_batch_id"], f"{label}.source_batch_id"
    )
    model_id = _required_clean_text(body["model_id"], f"{label}.model_id")
    model_version = _required_clean_text(
        body["model_version"], f"{label}.model_version"
    )

    raw_items = body["items"]
    if not isinstance(raw_items, list):
        raise RuntimeForgeBridgeError(f"{label}.items must be an array")
    if not 1 <= len(raw_items) <= BATCH_MAX_ITEMS:
        raise RuntimeForgeBridgeError(
            f"{label}.items must contain 1-{BATCH_MAX_ITEMS} items"
        )

    responses: list[forge_bridge.ForgeResponseArtifact] = []
    response_hashes: list[str] = []
    seen_hashes: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        item = _closed_object(
            raw_item,
            BATCH_RESPONSE_ITEM_KEYS,
            f"{label}.items[{index}]",
        )
        request_hash = _required_sha256(
            item["generation_request_sha256"],
            f"{label}.items[{index}].generation_request_sha256",
        )
        if request_hash in seen_hashes:
            raise RuntimeForgeBridgeError(f"{label} contains a duplicate response hash")
        seen_hashes.add(request_hash)
        if not isinstance(item["structured_output"], dict):
            raise RuntimeForgeBridgeError(
                f"{label}.items[{index}].structured_output must be an object"
            )

        standard_body = {
            "artifact": forge_bridge.RESPONSE_ARTIFACT,
            "artifact_version": forge_bridge.ARTIFACT_VERSION,
            "generation_request_sha256": request_hash,
            "model_id": model_id,
            "model_version": model_version,
            "structured_output": item["structured_output"],
        }
        response = forge_bridge.parse_response_artifact(
            canonical_json_bytes(standard_body)
        )
        responses.append(response)
        response_hashes.append(request_hash)

    if source_batch_id != _batch_id_from_hashes(response_hashes):
        raise RuntimeForgeBridgeError(
            f"{label}.source_batch_id does not match its items"
        )
    return BatchResponseArtifact(source_batch_id, tuple(responses))


def bind_batch(
    request_artifact: BatchRequestArtifact,
    response_artifact: BatchResponseArtifact,
    prompt: forge_bridge.ForgePrompt,
) -> tuple[forge_bridge.BoundGeneration, ...]:
    """Bind every response to its request before the first Forge replay."""
    if request_artifact.batch_id != response_artifact.source_batch_id:
        raise RuntimeForgeBridgeError("batch response does not answer this batch")
    if len(request_artifact.requests) != len(response_artifact.responses):
        raise RuntimeForgeBridgeError("batch request/response item counts differ")

    bound: list[forge_bridge.BoundGeneration] = []
    for index, (request, response) in enumerate(
        zip(request_artifact.requests, response_artifact.responses, strict=True)
    ):
        if request.generation_request_sha256 != response.generation_request_sha256:
            raise RuntimeForgeBridgeError(
                f"batch item {index} generation_request_sha256 mismatch"
            )
        bound.append(forge_bridge.bind_generation(request, response, prompt))
    return tuple(bound)
