"""Pure construction and hashing helpers for T6 Forge."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from ..contracts import (
    ANKI_NOTE_TYPE_NAME,
    SOURCE_REF_PATTERN,
    UNIT_KEY_PATTERN,
    UNIT_KEY_SEPARATOR,
    UNIQUE_NOTE_FIELD,
)
from ..models import VocabUnit
from .request import ForgePreview, ForgeRequest, GenerationMetadata

_ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,128}")
_SOURCE_REF_RE = re.compile(SOURCE_REF_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def canonical_json_bytes(value: object) -> bytes:
    """Return the canonical UTF-8 JSON representation used by T6 hashes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_canonical(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def generation_request_sha256(request: ForgeRequest) -> str:
    return sha256_canonical(
        {
            "source_ref": request.source_ref,
            "source_sentence": request.source_sentence,
            "learner_note": request.learner_note,
        }
    )


def structured_output_sha256(output: Mapping[str, Any]) -> str:
    return sha256_canonical(dict(output))


def validate_generation_metadata(metadata: GenerationMetadata) -> None:
    for name in ("model_id", "model_version", "prompt_version"):
        value = getattr(metadata, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not isinstance(metadata.prompt_sha256, str) or _SHA256_RE.fullmatch(
        metadata.prompt_sha256
    ) is None:
        raise ValueError("prompt_sha256 must be lowercase 64-hex")
    if not isinstance(metadata.generation_config, Mapping):
        raise TypeError("generation_config must be a mapping")
    for key, value in metadata.generation_config.items():
        if not isinstance(key, str):
            raise TypeError("generation_config keys must be strings")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise TypeError("generation_config values must be JSON scalars")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("generation_config floats must be finite")
    canonical_json_bytes(dict(metadata.generation_config))


def validate_preflight(request: ForgeRequest, deck_name: str) -> bool:
    if not isinstance(request, ForgeRequest):
        raise TypeError("request must be a ForgeRequest")
    if not isinstance(deck_name, str) or not deck_name.strip():
        return False
    if not isinstance(request.source_ref, str) or _SOURCE_REF_RE.fullmatch(
        request.source_ref
    ) is None:
        return False
    if not isinstance(request.source_sentence, str) or not request.source_sentence.strip():
        return False
    if not isinstance(request.learner_note, str):
        return False
    return True


def build_candidate(
    request: ForgeRequest,
    output: Mapping[str, Any],
    *,
    today: date,
) -> VocabUnit:
    """Construct one Forge-stage VocabUnit without repairing generated data."""
    target = {channel: bool(output[f"target_{channel}"]) for channel in "RLWS"}
    return VocabUnit(
        unit_key=(
            str(output["lemma_slug"])
            + UNIT_KEY_SEPARATOR
            + str(output["sense_slug"])
        ),
        lemma=str(output["lemma"]),
        lemma_slug=str(output["lemma_slug"]),
        sense_slug=str(output["sense_slug"]),
        unit_type=str(output["unit_type"]),
        Target_R="1" if target["R"] else "",
        Target_L="1" if target["L"] else "",
        Target_W="1" if target["W"] else "",
        Target_S="1" if target["S"] else "",
        register=str(output["register"]),
        definition_en=str(output["definition_en"]),
        source_ref=request.source_ref,
        source_sentence=request.source_sentence,
        state_R="NEW" if target["R"] else "",
        state_L="NEW" if target["L"] else "",
        state_W="NEW" if target["W"] else "",
        state_S="NEW" if target["S"] else "",
        created=today.isoformat(),
    )


def identity_trusted(unit: VocabUnit) -> bool:
    if _UNIT_KEY_RE.fullmatch(unit.unit_key) is None:
        return False
    return unit.unit_key == (
        unit.lemma_slug + UNIT_KEY_SEPARATOR + unit.sense_slug
    )


def validate_attempt_id(value: object) -> str:
    if not isinstance(value, str) or _ATTEMPT_ID_RE.fullmatch(value) is None:
        raise ValueError("forge_attempt_id must match [A-Za-z0-9._-]{8,128}")
    return value


def unit_key_query(unit_key: str) -> str:
    return (
        f"note:{ANKI_NOTE_TYPE_NAME} "
        f"{UNIQUE_NOTE_FIELD}:re:^{re.escape(unit_key)}$"
    )


def build_preview(
    unit: VocabUnit,
    target_justification: Mapping[str, str],
) -> ForgePreview:
    targets = tuple(
        channel
        for channel in "RLWS"
        if getattr(unit, f"Target_{channel}") == "1"
    )
    states = tuple((channel, getattr(unit, f"state_{channel}")) for channel in targets)
    justifications = tuple(
        (channel, target_justification[channel])
        for channel in ("W", "S")
        if channel in target_justification
    )
    return ForgePreview(
        unit_key=unit.unit_key,
        lemma=unit.lemma,
        unit_type=unit.unit_type,
        register=unit.register,
        definition_en=unit.definition_en,
        source_ref=unit.source_ref,
        source_sentence=unit.source_sentence,
        targets=targets,
        states=states,
        target_justification=justifications,
    )
