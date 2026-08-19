"""Pure schema parsing and immutable preview types for T8 contexts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .contracts import CONTEXT_FIELDS


class ContextSchemaError(ValueError):
    """Raised when a generated context object violates the local schema."""


@dataclass(frozen=True, slots=True)
class ContextPreview:
    """Immutable candidate shown for initial human confirmation."""

    unit_key: str
    lemma: str
    definition_en: str
    register: str
    Ctx_1: str
    Ctx_2: str
    Ctx_3: str
    Ctx_4: str
    Ctx_5: str
    validation_passed: bool = field(default=True, init=False)

def parse_context_bank(value: object) -> dict[str, str]:
    """Strictly validate and copy one exact five-field context object."""
    if not isinstance(value, Mapping):
        raise ContextSchemaError("context output must be an object")

    actual_keys = set(value.keys())
    expected_keys = set(CONTEXT_FIELDS)
    if actual_keys != expected_keys:
        raise ContextSchemaError(
            "context output fields must match Ctx_1 through Ctx_5 exactly"
        )

    parsed: dict[str, str] = {}
    for field_name in CONTEXT_FIELDS:
        field_value = value[field_name]
        if not isinstance(field_value, str):
            raise ContextSchemaError(f"{field_name} must be a string")
        parsed[field_name] = field_value
    return parsed
