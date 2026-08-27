"""Strict provider-neutral structured-output schema for Forge."""

from __future__ import annotations

from collections.abc import Mapping

from ..contracts import REGISTER_VALUES, UNIT_TYPE_VALUES


FORGE_SCHEMA_VERSION = "1"

_STRING_FIELDS = (
    "lemma",
    "lemma_slug",
    "sense_slug",
    "unit_type",
    "register",
    "definition_en",
)
_TARGET_FIELDS = (
    "target_R",
    "target_L",
    "target_W",
    "target_S",
)
_REQUIRED_FIELDS = (*_STRING_FIELDS, *_TARGET_FIELDS, "target_justification")

FORGE_JSON_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(_REQUIRED_FIELDS),
    "properties": {
        "lemma": {"type": "string"},
        "lemma_slug": {"type": "string"},
        "sense_slug": {"type": "string"},
        "unit_type": {"type": "string", "enum": list(UNIT_TYPE_VALUES)},
        "register": {"type": "string", "enum": list(REGISTER_VALUES)},
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


class ForgeSchemaError(ValueError):
    """Raised when generated structured output violates the strict schema."""


def parse_strict_output(value: object) -> dict[str, object]:
    """Validate and copy one exact Forge structured-output object."""
    if not isinstance(value, Mapping):
        raise ForgeSchemaError("structured output must be an object")

    actual_keys = set(value.keys())
    required_keys = set(_REQUIRED_FIELDS)
    if actual_keys != required_keys:
        raise ForgeSchemaError("structured output fields do not match the schema")

    parsed: dict[str, object] = {}
    for field_name in _STRING_FIELDS:
        field_value = value[field_name]
        if not isinstance(field_value, str):
            raise ForgeSchemaError(f"{field_name} must be a string")
        parsed[field_name] = field_value

    if parsed["unit_type"] not in UNIT_TYPE_VALUES:
        raise ForgeSchemaError("unit_type is outside the schema enum")
    if parsed["register"] not in REGISTER_VALUES:
        raise ForgeSchemaError("register is outside the schema enum")

    for field_name in _TARGET_FIELDS:
        field_value = value[field_name]
        if type(field_value) is not bool:
            raise ForgeSchemaError(f"{field_name} must be a boolean")
        parsed[field_name] = field_value

    raw_justification = value["target_justification"]
    if not isinstance(raw_justification, Mapping):
        raise ForgeSchemaError("target_justification must be an object")
    if not set(raw_justification.keys()).issubset({"W", "S"}):
        raise ForgeSchemaError("target_justification has an extra field")

    justification: dict[str, str] = {}
    for channel in ("W", "S"):
        if channel not in raw_justification:
            continue
        reason = raw_justification[channel]
        if not isinstance(reason, str):
            raise ForgeSchemaError(
                f"target_justification.{channel} must be a string"
            )
        justification[channel] = reason
    parsed["target_justification"] = justification
    return parsed
