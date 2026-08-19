"""Pure request, schema, parsing, and preview types for T8 contexts."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from .contracts import CONTEXT_FIELDS


_CONTEXT_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(CONTEXT_FIELDS),
    "properties": {
        field_name: {"type": "string"}
        for field_name in CONTEXT_FIELDS
    },
}


class ContextSchemaError(ValueError):
    """Raised when a generated context object violates the local schema."""


@dataclass(frozen=True, slots=True)
class ContextGenerationRequest:
    """Only lexical/source data relevant to context generation."""

    lemma: str
    unit_type: str
    definition_en: str
    register: str
    source_sentence: str

    def __post_init__(self) -> None:
        for field_name in (
            "lemma",
            "unit_type",
            "definition_en",
            "register",
            "source_sentence",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")


class ContextGenerator(Protocol):
    """Provider-neutral context generation boundary."""

    def generate(
        self,
        request: ContextGenerationRequest,
        *,
        json_schema: Mapping[str, object],
    ) -> Mapping[str, object]: ...


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


def context_json_schema() -> dict[str, object]:
    """Return an independent deep copy of the strict context schema."""
    return deepcopy(_CONTEXT_JSON_SCHEMA)


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
