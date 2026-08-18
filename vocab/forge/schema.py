"""Strict structured-output contract for T6 Forge."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FORGE_SCHEMA_VERSION = "1"

FORGE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "lemma",
        "lemma_slug",
        "sense_slug",
        "unit_type",
        "register",
        "definition_en",
        "target_R",
        "target_L",
        "target_W",
        "target_S",
        "target_justification",
    ],
    "properties": {
        "lemma": {"type": "string"},
        "lemma_slug": {"type": "string"},
        "sense_slug": {"type": "string"},
        "unit_type": {"type": "string", "enum": ["word", "chunk", "frame"]},
        "register": {
            "type": "string",
            "enum": ["academic", "neutral", "conversational", "technical"],
        },
        "definition_en": {"type": "string"},
        "target_R": {"type": "boolean"},
        "target_L": {"type": "boolean"},
        "target_W": {"type": "boolean"},
        "target_S": {"type": "boolean"},
        "target_justification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "W": {"type": "string"},
                "S": {"type": "string"},
            },
        },
    },
}

_REQUIRED = tuple(FORGE_JSON_SCHEMA["required"])
_ALLOWED = frozenset(_REQUIRED)
_UNIT_TYPES = frozenset(("word", "chunk", "frame"))
_REGISTERS = frozenset(("academic", "neutral", "conversational", "technical"))
_TARGETS = ("target_R", "target_L", "target_W", "target_S")


class ForgeSchemaError(ValueError):
    """Raised when provider output violates the strict T6 output schema."""


def parse_forge_output(value: object) -> dict[str, Any]:
    """Validate and copy one provider-neutral Forge structured output."""
    if not isinstance(value, Mapping):
        raise ForgeSchemaError("Forge output must be a JSON object")

    keys = set(value)
    missing = [name for name in _REQUIRED if name not in value]
    extra = sorted(keys.difference(_ALLOWED))
    if missing:
        raise ForgeSchemaError(f"Forge output missing fields: {tuple(missing)}")
    if extra:
        raise ForgeSchemaError(f"Forge output contains extra fields: {tuple(extra)}")

    for field in ("lemma", "lemma_slug", "sense_slug", "definition_en"):
        if not isinstance(value[field], str):
            raise ForgeSchemaError(f"{field} must be a string")

    if value["unit_type"] not in _UNIT_TYPES:
        raise ForgeSchemaError("unit_type is outside the strict schema enum")
    if value["register"] not in _REGISTERS:
        raise ForgeSchemaError("register is outside the strict schema enum")

    for field in _TARGETS:
        if type(value[field]) is not bool:
            raise ForgeSchemaError(f"{field} must be a boolean")

    justification = value["target_justification"]
    if not isinstance(justification, Mapping):
        raise ForgeSchemaError("target_justification must be an object")
    unknown_justification = sorted(set(justification).difference(("W", "S")))
    if unknown_justification:
        raise ForgeSchemaError(
            "target_justification contains unsupported keys: "
            f"{tuple(unknown_justification)}"
        )
    if any(not isinstance(v, str) for v in justification.values()):
        raise ForgeSchemaError("target_justification values must be strings")

    result = {field: value[field] for field in _REQUIRED}
    result["target_justification"] = dict(justification)
    return result
