"""Pure tests for Anki card-template semantic verification."""

from __future__ import annotations

from copy import deepcopy

import pytest

from vocab.anki_template import verify_model_snapshot
from vocab.card_contract import TARGET_FIELD_BY_TEMPLATE_NAME
from vocab.contracts import (
    ANKI_NOTE_TYPE_NAME,
    ANKI_SORT_FIELD,
    CARD_TEMPLATE_NAMES,
    NOTE_FIELDS,
    NOVEL_CONTEXT_FIELDS,
)


def valid_model() -> dict[str, object]:
    field_ordinals = {name: index for index, name in enumerate(NOTE_FIELDS)}
    templates = []
    requirements = []
    for ordinal, template_name in enumerate(CARD_TEMPLATE_NAMES):
        target = TARGET_FIELD_BY_TEMPLATE_NAME[template_name]
        content = "Ctx_1" if template_name == "R" else "lemma"
        templates.append(
            {
                "name": template_name,
                "ord": ordinal,
                "qfmt": (
                    f"<div>{{{{#{target}}}}}<span>{{{{ {content} }}}}</span>"
                    f"{{{{/{target}}}}}</div>"
                ),
                "afmt": (
                    "{{FrontSide}}<hr id=answer>{{ definition_en }}"
                ),
            }
        )
        requirements.append(
            [ordinal, "any", [field_ordinals[target]]]
        )

    return {
        "id": 1704387367119,
        "name": ANKI_NOTE_TYPE_NAME,
        "sortf": field_ordinals[ANKI_SORT_FIELD],
        "flds": [
            {"name": name, "ord": ordinal}
            for ordinal, name in enumerate(NOTE_FIELDS)
        ],
        "tmpls": templates,
        "req": requirements,
        "css": ".card { color: black; }",
    }


def template(model: dict[str, object], name: str) -> dict[str, object]:
    templates = model["tmpls"]
    assert isinstance(templates, list)
    return next(item for item in templates if item["name"] == name)


def codes(model: object) -> tuple[str, ...]:
    return tuple(
        violation.code for violation in verify_model_snapshot(model)
    )


def test_valid_model_passes() -> None:
    assert verify_model_snapshot(valid_model()) == ()


def test_wrong_model_identity_fails() -> None:
    model = valid_model()
    model["name"] = "OtherModel"

    assert "MODEL_NAME_MISMATCH" in codes(model)


def test_harmless_whitespace_html_wrappers_and_css_changes_pass() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = """
        <main class="review"><section>
        {{#Target_R}}<div>{{ text:Ctx_1 }}</div>{{/Target_R}}
        </section></main>
    """
    model["css"] = "body.nightMode .card { color: rebeccapurple; }"

    assert verify_model_snapshot(model) == ()


def test_template_response_order_does_not_matter() -> None:
    model = valid_model()
    model["tmpls"] = list(reversed(model["tmpls"]))

    assert verify_model_snapshot(model) == ()


def test_explicit_anki_virtual_fields_are_allowed() -> None:
    model = valid_model()
    template(model, "R")["afmt"] = (
        "{{FrontSide}}{{Tags}}{{Type}}{{Deck}}{{Subdeck}}{{Card}}"
    )

    assert verify_model_snapshot(model) == ()


@pytest.mark.parametrize("side", ["qfmt", "afmt"], ids=["front", "back"])
def test_empty_template_side_fails(side: str) -> None:
    model = valid_model()
    template(model, "R")[side] = "  \n  "

    expected = (
        "TEMPLATE_FRONT_EMPTY" if side == "qfmt" else "TEMPLATE_BACK_EMPTY"
    )
    assert expected in codes(model)


def test_wrong_target_gate_fails() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_L}}{{Ctx_1}}{{/Target_L}}"
    )

    result = codes(model)
    assert "TEMPLATE_TARGET_GATE_MISSING" in result
    assert "TEMPLATE_TARGET_GATE_WRONG" in result


def test_missing_target_gate_fails() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = "{{Ctx_1}}"

    assert "TEMPLATE_TARGET_GATE_MISSING" in codes(model)


def test_inverted_target_gate_fails() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{^Target_R}}{{Ctx_1}}{{/Target_R}}"
    )

    assert "TEMPLATE_TARGET_GATE_INVERTED" in codes(model)


def test_multiple_target_gates_fail() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}{{/Target_R}}"
        "{{#Target_R}}again{{/Target_R}}"
    )

    assert "TEMPLATE_TARGET_GATE_MULTIPLE" in codes(model)


@pytest.mark.parametrize(
    "outside",
    ["visible text", "{{lemma}}", "<img src=visible.png>"],
    ids=["literal", "field", "rendering-tag"],
)
def test_content_outside_target_gate_fails(outside: str) -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        f"{outside}{{{{#Target_R}}}}{{{{Ctx_1}}}}{{{{/Target_R}}}}"
    )

    assert "TEMPLATE_CONTENT_OUTSIDE_TARGET_GATE" in codes(model)


@pytest.mark.parametrize("field_name", NOVEL_CONTEXT_FIELDS)
@pytest.mark.parametrize("side", ["qfmt", "afmt"], ids=["front", "back"])
def test_novel_contexts_are_forbidden_on_either_side(
    field_name: str,
    side: str,
) -> None:
    model = valid_model()
    if side == "qfmt":
        template(model, "R")[side] = (
            "{{#Target_R}}{{Ctx_1}}"
            f"{{{{{field_name}}}}}"
            "{{/Target_R}}"
        )
    else:
        template(model, "R")[side] += f"{{{{{field_name}}}}}"

    assert "TEMPLATE_NOVEL_CONTEXT_FORBIDDEN" in codes(model)


def test_mustache_reference_inside_html_comment_still_counts() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}<!-- {{Ctx_2}} -->{{/Target_R}}"
    )

    assert "TEMPLATE_NOVEL_CONTEXT_FORBIDDEN" in codes(model)


def test_r_front_requires_ctx_1() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{lemma}}{{/Target_R}}"
    )

    assert "TEMPLATE_REQUIRED_FRONT_FIELD_MISSING" in codes(model)


def test_unknown_field_fails() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}{{unknown_field}}{{/Target_R}}"
    )

    assert "TEMPLATE_UNKNOWN_FIELD" in codes(model)


def test_target_r_extra_is_not_resolved_as_target_r() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}{{Target_R_extra}}{{/Target_R}}"
    )

    assert "TEMPLATE_UNKNOWN_FIELD" in codes(model)


def test_unbalanced_mustache_section_fails_closed() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = "{{#Target_R}}{{Ctx_1}}"

    assert "MUSTACHE_UNCLOSED_SECTION" in codes(model)


@pytest.mark.parametrize(
    ("markup", "expected_code"),
    [
        (
            "<ScRiPt>alert(1)</sCrIpT>",
            "TEMPLATE_SCRIPT_TAG_FORBIDDEN",
        ),
        (
            '<img OnErRoR="alert(1)" src="x">',
            "TEMPLATE_INLINE_HANDLER_FORBIDDEN",
        ),
    ],
    ids=["script", "inline-handler"],
)
def test_executable_javascript_fails(markup: str, expected_code: str) -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        f"{{{{#Target_R}}}}{{{{Ctx_1}}}}{markup}{{{{/Target_R}}}}"
    )

    assert expected_code in codes(model)


def test_visible_javascript_scheme_text_passes() -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}"
        "<div>javascript: is a URI scheme</div>"
        "{{/Target_R}}"
    )

    assert verify_model_snapshot(model) == ()


@pytest.mark.parametrize(
    "attribute_value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java&#x73;cript:alert(1)",
    ],
    ids=["lowercase", "mixed-case", "entity-encoded"],
)
def test_javascript_href_fails(attribute_value: str) -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}"
        f'<a href="{attribute_value}">open</a>'
        "{{/Target_R}}"
    )

    assert "TEMPLATE_JAVASCRIPT_URL_FORBIDDEN" in codes(model)


@pytest.mark.parametrize(
    "attribute_name",
    ["href", "src", "action", "formaction", "xlink:href"],
)
def test_javascript_url_attributes_fail(attribute_name: str) -> None:
    model = valid_model()
    template(model, "R")["qfmt"] = (
        "{{#Target_R}}{{Ctx_1}}"
        f'<div {attribute_name}="javascript:alert(1)">open</div>'
        "{{/Target_R}}"
    )

    assert "TEMPLATE_JAVASCRIPT_URL_FORBIDDEN" in codes(model)


def test_wrong_sort_field_fails() -> None:
    model = valid_model()
    model["sortf"] = NOTE_FIELDS.index("lemma")

    assert "MODEL_SORT_FIELD_MISMATCH" in codes(model)


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "renamed"],
)
def test_template_name_contract_fails(change: str) -> None:
    model = valid_model()
    templates = model["tmpls"]
    assert isinstance(templates, list)
    if change == "missing":
        templates.pop()
    elif change == "extra":
        extra = deepcopy(templates[-1])
        extra["name"] = "Extra"
        extra["ord"] = 99
        templates.append(extra)
    else:
        templates[0]["name"] = "Renamed"

    assert "MODEL_TEMPLATE_NAMES_MISMATCH" in codes(model)


def test_wrong_field_order_fails() -> None:
    model = valid_model()
    fields = model["flds"]
    assert isinstance(fields, list)
    fields[0], fields[1] = fields[1], fields[0]

    assert "MODEL_FIELD_ORDER_MISMATCH" in codes(model)


def test_inconsistent_field_ordinals_fail_closed() -> None:
    model = valid_model()
    fields = model["flds"]
    assert isinstance(fields, list)
    fields[1]["ord"], fields[2]["ord"] = fields[2]["ord"], fields[1]["ord"]

    assert "MODEL_FIELDS_MALFORMED" in codes(model)


@pytest.mark.parametrize(
    "malformed_req",
    [
        None,
        [[0, "any"]],
        [["0", "any", [5]]],
        [[0, "sometimes", [5]]],
        [[0, "any", ["5"]]],
        [[0, "none", [5]]],
    ],
)
def test_malformed_req_fails_closed(malformed_req: object) -> None:
    model = valid_model()
    model["req"] = malformed_req

    assert "MODEL_REQ_MALFORMED" in codes(model)


def test_missing_req_branch_fails_closed() -> None:
    model = valid_model()
    model["req"].pop()

    assert "MODEL_REQ_TEMPLATE_MISSING" in codes(model)


@pytest.mark.parametrize(
    ("mode", "required_fields"),
    [
        ("any", ("Target_R", "Ctx_1")),
        ("all", ("Ctx_1",)),
        ("none", ()),
    ],
    ids=["alternative-any", "missing-from-all", "none"],
)
def test_req_must_make_target_necessary(
    mode: str,
    required_fields: tuple[str, ...],
) -> None:
    model = valid_model()
    model["req"][0] = [
        0,
        mode,
        [NOTE_FIELDS.index(field) for field in required_fields],
    ]

    assert "MODEL_REQ_TARGET_NOT_NECESSARY" in codes(model)


def test_req_allows_additional_legitimate_requirements() -> None:
    model = valid_model()
    model["req"][0] = [
        0,
        "all",
        [NOTE_FIELDS.index("Target_R"), NOTE_FIELDS.index("Ctx_1")],
    ]

    assert verify_model_snapshot(model) == ()


def test_duplicate_req_record_for_template_fails_closed() -> None:
    model = valid_model()
    model["req"].append(
        [0, "any", [NOTE_FIELDS.index("Target_R")]]
    )

    assert "MODEL_REQ_MALFORMED" in codes(model)


def test_exactly_one_req_record_per_template_passes() -> None:
    model = valid_model()

    assert len(model["req"]) == len(CARD_TEMPLATE_NAMES)
    assert verify_model_snapshot(model) == ()


def test_absent_req_metadata_fails_closed() -> None:
    model = valid_model()
    del model["req"]

    assert "MODEL_REQ_MALFORMED" in codes(model)
