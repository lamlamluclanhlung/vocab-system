"""Pure tests for T8 context request, schema, parser, and preview types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from vocab.context import (
    ContextGenerationRequest,
    ContextPreview,
    ContextSchemaError,
    context_json_schema,
    parse_context_bank,
)
from vocab.contracts import CONTEXT_FIELDS


def valid_bank() -> dict[str, str]:
    return {
        field_name: f"context {index}"
        for index, field_name in enumerate(CONTEXT_FIELDS, start=1)
    }


def test_context_schema_has_exact_five_string_fields() -> None:
    schema = context_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert tuple(schema["required"]) == CONTEXT_FIELDS
    assert set(schema["properties"]) == set(CONTEXT_FIELDS)
    assert all(
        property_schema == {"type": "string"}
        for property_schema in schema["properties"].values()
    )


def test_parser_rejects_additional_property() -> None:
    bank = valid_bank()
    bank["extra"] = "not allowed"

    with pytest.raises(ContextSchemaError, match="exactly"):
        parse_context_bank(bank)


def test_parser_rejects_missing_field() -> None:
    bank = valid_bank()
    del bank["Ctx_5"]

    with pytest.raises(ContextSchemaError, match="exactly"):
        parse_context_bank(bank)


@pytest.mark.parametrize("value", [None, 7, True, ["text"]])
def test_parser_rejects_non_string_without_coercion(value: object) -> None:
    bank = valid_bank()
    bank["Ctx_3"] = value  # type: ignore[assignment]

    with pytest.raises(ContextSchemaError, match="Ctx_3 must be a string"):
        parse_context_bank(bank)


def test_parser_copies_without_mutating_input() -> None:
    bank = valid_bank()
    original = dict(bank)

    parsed = parse_context_bank(bank)
    parsed["Ctx_1"] = "changed result"

    assert bank == original


def test_schema_calls_return_independent_deep_copies() -> None:
    first = context_json_schema()
    first["properties"]["Ctx_1"]["type"] = "integer"
    first["required"].append("extra")

    second = context_json_schema()

    assert second["properties"]["Ctx_1"] == {"type": "string"}
    assert tuple(second["required"]) == CONTEXT_FIELDS


def test_generation_request_contains_only_allowed_lexical_data() -> None:
    assert tuple(field.name for field in fields(ContextGenerationRequest)) == (
        "lemma",
        "unit_type",
        "definition_en",
        "register",
        "source_sentence",
    )


def test_context_preview_is_immutable_and_validation_is_fixed_true() -> None:
    preview = ContextPreview(
        unit_key="subtle::small-difference",
        lemma="subtle",
        definition_en="hard to notice",
        register="neutral",
        **valid_bank(),
    )

    assert preview.validation_passed is True
    with pytest.raises(FrozenInstanceError):
        preview.Ctx_1 = "replacement"  # type: ignore[misc]
