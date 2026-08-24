"""Canonical hashing and FORGE event-payload construction."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..artifact_json import canonical_json_bytes, canonical_sha256
from .request import (
    JSONScalar,
    PRODUCER_VERSION,
    ForgeRequest,
    GenerationMetadata,
)
from .schema import FORGE_SCHEMA_VERSION


_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,128}")


def is_valid_attempt_id(value: object) -> bool:
    return isinstance(value, str) and _ATTEMPT_ID_RE.fullmatch(value) is not None


def is_lower_sha256(value: object) -> bool:
    return isinstance(value, str) and _LOWER_SHA256_RE.fullmatch(value) is not None


def validate_generation_metadata(metadata: object) -> GenerationMetadata:
    """Validate metadata and detach its mutable generation-config mapping."""
    if not isinstance(metadata, GenerationMetadata):
        raise ValueError("generation_metadata must be GenerationMetadata")

    for field_name in ("model_id", "model_version", "prompt_version"):
        value = getattr(metadata, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    if not is_lower_sha256(metadata.prompt_sha256):
        raise ValueError("prompt_sha256 must be 64 lowercase hexadecimal characters")
    if not isinstance(metadata.generation_config, Mapping):
        raise ValueError("generation_config must be a mapping")

    config: dict[str, JSONScalar] = {}
    for key, value in metadata.generation_config.items():
        if not isinstance(key, str):
            raise ValueError("generation_config keys must be strings")
        if value is None or isinstance(value, (str, bool)):
            config[key] = value
        elif type(value) is int:
            config[key] = value
        elif type(value) is float and math.isfinite(value):
            config[key] = value
        else:
            raise ValueError("generation_config values must be finite JSON scalars")

    canonical_json_bytes(config)
    return GenerationMetadata(
        model_id=metadata.model_id,
        model_version=metadata.model_version,
        prompt_version=metadata.prompt_version,
        prompt_sha256=metadata.prompt_sha256,
        generation_config=config,
    )


@dataclass(frozen=True, slots=True)
class ForgeProvenance:
    metadata: GenerationMetadata
    generation_request_sha256: str
    structured_output_sha256: str
    structured_output: dict[str, object]


def build_provenance(
    request: ForgeRequest,
    metadata: GenerationMetadata,
    structured_output: Mapping[str, object],
) -> ForgeProvenance:
    request_value = {
        "source_ref": request.source_ref,
        "source_sentence": request.source_sentence,
        "learner_note": request.learner_note,
    }
    detached_output = copy.deepcopy(dict(structured_output))
    return ForgeProvenance(
        metadata=metadata,
        generation_request_sha256=canonical_sha256(request_value),
        structured_output_sha256=canonical_sha256(detached_output),
        structured_output=detached_output,
    )


def evidence_payload(
    *,
    request: ForgeRequest,
    forge_attempt_id: str,
    provenance: ForgeProvenance,
) -> dict[str, object]:
    structured_output = copy.deepcopy(provenance.structured_output)
    return {
        "source_ref": request.source_ref,
        "accepted": False,
        "forge_attempt_id": forge_attempt_id,
        "model_id": provenance.metadata.model_id,
        "model_version": provenance.metadata.model_version,
        "prompt_version": provenance.metadata.prompt_version,
        "prompt_sha256": provenance.metadata.prompt_sha256,
        "forge_schema_version": FORGE_SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
        "generation_config": dict(provenance.metadata.generation_config),
        "generation_request_sha256": provenance.generation_request_sha256,
        "structured_output_sha256": provenance.structured_output_sha256,
        "structured_output": structured_output,
        "target_justification": dict(
            structured_output["target_justification"]
        ),
    }


def rejection_payload(
    *,
    request: ForgeRequest,
    forge_attempt_id: str,
    provenance: ForgeProvenance,
    outcome: str,
    violations: tuple[str, ...] = (),
    decided_by: str | None = None,
    duplicate_note_ids: tuple[int, ...] = (),
) -> dict[str, object]:
    payload = evidence_payload(
        request=request,
        forge_attempt_id=forge_attempt_id,
        provenance=provenance,
    )
    payload["outcome"] = outcome
    if violations:
        payload["violations"] = list(violations)
    if decided_by is not None:
        payload["decided_by"] = decided_by
    if duplicate_note_ids:
        payload["duplicate_note_ids"] = list(duplicate_note_ids)
    return payload


def commit_intent_payload(
    *,
    request: ForgeRequest,
    forge_attempt_id: str,
    provenance: ForgeProvenance,
    confirmed_by: str,
) -> dict[str, object]:
    payload = evidence_payload(
        request=request,
        forge_attempt_id=forge_attempt_id,
        provenance=provenance,
    )
    payload.update(
        {
            "outcome": "COMMIT_INTENT",
            "confirmed_by": confirmed_by,
        }
    )
    return payload


def acceptance_payload(
    *,
    source_ref: str,
    forge_attempt_id: str,
    note_id: int,
    structured_output_sha256: str,
    repaired: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_ref": source_ref,
        "accepted": True,
        "forge_attempt_id": forge_attempt_id,
        "note_id": note_id,
        "structured_output_sha256": structured_output_sha256,
    }
    if repaired:
        payload.update(
            {
                "repaired": True,
                "repair_reason": "recovered-from-commit-intent",
            }
        )
    return payload
