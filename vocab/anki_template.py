"""Pure parsing and semantic verification for Anki model snapshots."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from .card_contract import (
    ANKI_VIRTUAL_FIELDS,
    FORBIDDEN_NORMAL_REVIEW_FIELDS,
    GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME,
    PERSISTED_CARD_FIELDS,
    REQUIRED_FRONT_FIELDS_BY_TEMPLATE_NAME,
    TARGET_FIELD_BY_TEMPLATE_NAME,
    TARGET_TEMPLATE_FIELDS,
)
from .contracts import (
    ANKI_NOTE_TYPE_NAME,
    ANKI_SORT_FIELD,
    CARD_TEMPLATE_NAMES,
    NOTE_FIELDS,
    NOVEL_CONTEXT_FIELDS,
)
from .media_contract import RESERVED_AUDIO_FIELDS


@dataclass(frozen=True, slots=True)
class AnkiTemplateViolation:
    """One deterministic model or card-template contract violation."""

    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.location}: {self.message}"


@dataclass(frozen=True, slots=True)
class MustacheSection:
    """One parsed Mustache section ancestor."""

    field_name: str
    inverted: bool


@dataclass(frozen=True, slots=True)
class MustacheReference:
    """One interpolation or section-opening field reference."""

    field_name: str
    kind: str
    inverted: bool
    ancestors: tuple[MustacheSection, ...]
    position: int


@dataclass(frozen=True, slots=True)
class MustacheText:
    """Literal template text and the sections containing it."""

    text: str
    ancestors: tuple[MustacheSection, ...]


@dataclass(frozen=True, slots=True)
class MustacheParseViolation:
    """One deterministic Mustache syntax violation."""

    code: str
    position: int
    message: str


@dataclass(frozen=True, slots=True)
class ParsedMustache:
    """Semantic Mustache tokens without rendering the template."""

    references: tuple[MustacheReference, ...]
    texts: tuple[MustacheText, ...]
    field_names: tuple[str, ...]
    violations: tuple[MustacheParseViolation, ...]


@dataclass(frozen=True, slots=True)
class _OpenSection:
    field_name: str
    inverted: bool
    position: int


@dataclass(frozen=True, slots=True)
class _TemplateRecord:
    name: str
    ordinal: int
    qfmt: str
    afmt: str


_SCRIPT_TAG_RE = re.compile(
    r"<\s*/?\s*script(?=[\s/>])",
    re.IGNORECASE,
)
_INLINE_HANDLER_RE = re.compile(
    r"<[^>]*(?:\s|/)+on[a-z][a-z0-9_:-]*\s*=",
    re.IGNORECASE | re.DOTALL,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_STYLE_BLOCK_RE = re.compile(
    r"<\s*style\b[^>]*>.*?<\s*/\s*style\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(
    r"<\s*/?\s*([a-z][a-z0-9:-]*)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)

# Container and inline-formatting tags do not themselves keep a front visibly
# non-empty. Void/media tags are intentionally absent from this allowlist.
_NON_CONTENT_HTML_TAGS = frozenset(
    {
        "article",
        "b",
        "big",
        "blockquote",
        "body",
        "center",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "font",
        "footer",
        "header",
        "html",
        "i",
        "li",
        "main",
        "mark",
        "ol",
        "p",
        "rp",
        "rt",
        "ruby",
        "s",
        "section",
        "small",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

_URL_ATTRIBUTES = frozenset(
    {"href", "src", "action", "formaction", "xlink:href"}
)


class _JavascriptURLAttributeParser(HTMLParser):
    """Detect javascript: schemes only in selected HTML URL attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found = False

    def handle_starttag(
        self,
        _tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        for name, value in attrs:
            if name.casefold() not in _URL_ATTRIBUTES or value is None:
                continue
            normalized = html.unescape(value).lstrip().casefold()
            if normalized.startswith("javascript:"):
                self.found = True
                return


def parse_mustache(template: str) -> ParsedMustache:
    """Parse supported Anki Mustache tokens without modifying HTML comments."""
    if not isinstance(template, str):
        raise TypeError("template must be a string")

    references: list[MustacheReference] = []
    texts: list[MustacheText] = []
    field_names: list[str] = []
    violations: list[MustacheParseViolation] = []
    stack: list[_OpenSection] = []
    cursor = 0

    def ancestors() -> tuple[MustacheSection, ...]:
        return tuple(
            MustacheSection(frame.field_name, frame.inverted)
            for frame in stack
        )

    while cursor < len(template):
        opening = template.find("{{", cursor)
        if opening < 0:
            texts.append(MustacheText(template[cursor:], ancestors()))
            cursor = len(template)
            break

        texts.append(MustacheText(template[cursor:opening], ancestors()))
        closing = template.find("}}", opening + 2)
        if closing < 0:
            violations.append(
                MustacheParseViolation(
                    "MUSTACHE_UNCLOSED_TAG",
                    opening,
                    "Mustache tag has no closing braces",
                )
            )
            texts.append(MustacheText(template[opening:], ancestors()))
            cursor = len(template)
            break

        raw_body = template[opening + 2 : closing]
        body = raw_body.strip()
        cursor = closing + 2
        if not body:
            violations.append(
                MustacheParseViolation(
                    "MUSTACHE_EMPTY_TAG",
                    opening,
                    "Mustache tag is empty",
                )
            )
            continue

        marker = body[0]
        if marker in {"#", "^", "/"}:
            field_name = body[1:].strip()
            if not field_name or ":" in field_name:
                violations.append(
                    MustacheParseViolation(
                        "MUSTACHE_MALFORMED_FIELD",
                        opening,
                        f"section field is malformed: {field_name!r}",
                    )
                )
                continue

            field_names.append(field_name)
            if marker == "/":
                if not stack:
                    violations.append(
                        MustacheParseViolation(
                            "MUSTACHE_UNMATCHED_CLOSE",
                            opening,
                            f"section {field_name!r} has no opening tag",
                        )
                    )
                elif stack[-1].field_name != field_name:
                    violations.append(
                        MustacheParseViolation(
                            "MUSTACHE_SECTION_MISMATCH",
                            opening,
                            f"section {field_name!r} closes "
                            f"{stack[-1].field_name!r}",
                        )
                    )
                else:
                    stack.pop()
                continue

            inverted = marker == "^"
            references.append(
                MustacheReference(
                    field_name=field_name,
                    kind="section",
                    inverted=inverted,
                    ancestors=ancestors(),
                    position=opening,
                )
            )
            stack.append(_OpenSection(field_name, inverted, opening))
            continue

        if marker in {"!", "&", ">", "="}:
            violations.append(
                MustacheParseViolation(
                    "MUSTACHE_UNSUPPORTED_TAG",
                    opening,
                    f"unsupported Mustache tag: {body!r}",
                )
            )
            continue

        filter_parts = tuple(part.strip() for part in body.split(":"))
        if any(not part for part in filter_parts):
            violations.append(
                MustacheParseViolation(
                    "MUSTACHE_MALFORMED_FIELD",
                    opening,
                    f"field/filter expression is malformed: {body!r}",
                )
            )
            continue

        field_name = filter_parts[-1]
        field_names.append(field_name)
        references.append(
            MustacheReference(
                field_name=field_name,
                kind="field",
                inverted=False,
                ancestors=ancestors(),
                position=opening,
            )
        )

    for frame in stack:
        violations.append(
            MustacheParseViolation(
                "MUSTACHE_UNCLOSED_SECTION",
                frame.position,
                f"section {frame.field_name!r} has no closing tag",
            )
        )

    return ParsedMustache(
        references=tuple(references),
        texts=tuple(texts),
        field_names=tuple(field_names),
        violations=tuple(violations),
    )


def verify_model_snapshot(snapshot: object) -> tuple[AnkiTemplateViolation, ...]:
    """Return every deterministic T3 violation in stable contract order."""
    violations: list[AnkiTemplateViolation] = []
    if not isinstance(snapshot, dict):
        return (
            AnkiTemplateViolation(
                "MODEL_SNAPSHOT_MALFORMED",
                "model",
                "model snapshot must be an object",
            ),
        )

    if snapshot.get("name") != ANKI_NOTE_TYPE_NAME:
        violations.append(
            AnkiTemplateViolation(
                "MODEL_NAME_MISMATCH",
                "model.name",
                f"expected {ANKI_NOTE_TYPE_NAME!r}, got {snapshot.get('name')!r}",
            )
        )

    field_names, field_ordinals = _verify_fields(snapshot, violations)
    _verify_sort_field(snapshot, field_ordinals, violations)
    templates = _verify_templates(snapshot, violations)

    records_by_name: dict[str, list[_TemplateRecord]] = {}
    for record in templates:
        records_by_name.setdefault(record.name, []).append(record)

    for template_name in CARD_TEMPLATE_NAMES:
        matching = records_by_name.get(template_name, [])
        if len(matching) != 1:
            continue
        _verify_template_semantics(matching[0], violations)

    if "req" not in snapshot:
        violations.append(
            AnkiTemplateViolation(
                "MODEL_REQ_MALFORMED",
                "model.req",
                "req metadata is required in a rich model snapshot",
            )
        )
    else:
        _verify_requirements(
            snapshot["req"],
            templates,
            field_names,
            field_ordinals,
            violations,
        )

    return tuple(violations)


def _verify_fields(
    snapshot: dict[str, Any],
    violations: list[AnkiTemplateViolation],
) -> tuple[tuple[str, ...], dict[str, int]]:
    fields = snapshot.get("flds")
    if not isinstance(fields, list):
        violations.append(
            AnkiTemplateViolation(
                "MODEL_FIELDS_MALFORMED",
                "model.flds",
                "field metadata must be a list",
            )
        )
        return (), {}

    parsed: list[tuple[str, int]] = []
    malformed = False
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            malformed = True
            continue
        name = field.get("name")
        ordinal = field.get("ord")
        if (
            not isinstance(name, str)
            or not name
            or type(ordinal) is not int
            or ordinal < 0
        ):
            malformed = True
            continue
        parsed.append((name, ordinal))

    names = tuple(name for name, _ordinal in parsed)
    ordinals = tuple(ordinal for _name, ordinal in parsed)
    if (
        malformed
        or len(parsed) != len(fields)
        or len(set(ordinals)) != len(ordinals)
        or ordinals != tuple(range(len(fields)))
    ):
        violations.append(
            AnkiTemplateViolation(
                "MODEL_FIELDS_MALFORMED",
                "model.flds",
                "every field needs a non-empty name and unique integer ordinal",
            )
        )

    if names != NOTE_FIELDS:
        missing = tuple(field for field in NOTE_FIELDS if field not in names)
        extra = tuple(field for field in names if field not in NOTE_FIELDS)
        violations.append(
            AnkiTemplateViolation(
                "MODEL_FIELD_ORDER_MISMATCH",
                "model.flds",
                "field order does not match NOTE_FIELDS; "
                f"missing={missing}, extra={extra}, actual={names}",
            )
        )

    return names, {name: ordinal for name, ordinal in parsed}


def _verify_sort_field(
    snapshot: dict[str, Any],
    field_ordinals: dict[str, int],
    violations: list[AnkiTemplateViolation],
) -> None:
    sort_ordinal = snapshot.get("sortf")
    if type(sort_ordinal) is not int or sort_ordinal < 0:
        violations.append(
            AnkiTemplateViolation(
                "MODEL_SORT_FIELD_MALFORMED",
                "model.sortf",
                "sortf must be a non-negative integer field ordinal",
            )
        )
        return

    actual_name = next(
        (
            field_name
            for field_name, ordinal in field_ordinals.items()
            if ordinal == sort_ordinal
        ),
        None,
    )
    if actual_name != ANKI_SORT_FIELD:
        violations.append(
            AnkiTemplateViolation(
                "MODEL_SORT_FIELD_MISMATCH",
                "model.sortf",
                f"sort field must be {ANKI_SORT_FIELD!r}, got {actual_name!r}",
            )
        )


def _verify_templates(
    snapshot: dict[str, Any],
    violations: list[AnkiTemplateViolation],
) -> tuple[_TemplateRecord, ...]:
    raw_templates = snapshot.get("tmpls")
    if not isinstance(raw_templates, list):
        violations.append(
            AnkiTemplateViolation(
                "MODEL_TEMPLATES_MALFORMED",
                "model.tmpls",
                "template metadata must be a list",
            )
        )
        return ()

    records: list[_TemplateRecord] = []
    malformed = False
    for template in raw_templates:
        if not isinstance(template, dict):
            malformed = True
            continue
        name = template.get("name")
        ordinal = template.get("ord")
        qfmt = template.get("qfmt")
        afmt = template.get("afmt")
        if (
            not isinstance(name, str)
            or not name
            or type(ordinal) is not int
            or ordinal < 0
            or not isinstance(qfmt, str)
            or not isinstance(afmt, str)
        ):
            malformed = True
            continue
        records.append(_TemplateRecord(name, ordinal, qfmt, afmt))

    ordinals = tuple(record.ordinal for record in records)
    if (
        malformed
        or len(records) != len(raw_templates)
        or len(set(ordinals)) != len(ordinals)
    ):
        violations.append(
            AnkiTemplateViolation(
                "MODEL_TEMPLATES_MALFORMED",
                "model.tmpls",
                "every template needs name, unique ordinal, qfmt, and afmt",
            )
        )

    actual_names = tuple(record.name for record in records)
    if (
        len(actual_names) != len(CARD_TEMPLATE_NAMES)
        or set(actual_names) != set(CARD_TEMPLATE_NAMES)
    ):
        missing = tuple(
            name for name in CARD_TEMPLATE_NAMES if name not in actual_names
        )
        extra = tuple(
            name for name in actual_names if name not in CARD_TEMPLATE_NAMES
        )
        violations.append(
            AnkiTemplateViolation(
                "MODEL_TEMPLATE_NAMES_MISMATCH",
                "model.tmpls",
                "template names do not match CARD_TEMPLATE_NAMES; "
                f"missing={missing}, extra={extra}, actual={actual_names}",
            )
        )

    return tuple(records)


def _verify_template_semantics(
    record: _TemplateRecord,
    violations: list[AnkiTemplateViolation],
) -> None:
    front_location = f"templates.{record.name}.front"
    back_location = f"templates.{record.name}.back"

    if not record.qfmt.strip():
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_FRONT_EMPTY",
                front_location,
                "front template must be non-empty",
            )
        )
    if not record.afmt.strip():
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_BACK_EMPTY",
                back_location,
                "back template must be non-empty",
            )
        )

    parsed_front = parse_mustache(record.qfmt)
    parsed_back = parse_mustache(record.afmt)
    _add_parse_violations(parsed_front, front_location, violations)
    _add_parse_violations(parsed_back, back_location, violations)
    _verify_side_references(parsed_front, front_location, violations)
    _verify_side_references(parsed_back, back_location, violations)
    _verify_javascript(record.qfmt, front_location, violations)
    _verify_javascript(record.afmt, back_location, violations)
    _verify_target_gate(record.name, parsed_front, front_location, violations)

    referenced_front_fields = set(parsed_front.field_names)
    for required_field in REQUIRED_FRONT_FIELDS_BY_TEMPLATE_NAME[record.name]:
        if required_field not in referenced_front_fields:
            violations.append(
                AnkiTemplateViolation(
                    "TEMPLATE_REQUIRED_FRONT_FIELD_MISSING",
                    front_location,
                    f"front must reference {required_field!r}",
                )
            )


def _add_parse_violations(
    parsed: ParsedMustache,
    location: str,
    violations: list[AnkiTemplateViolation],
) -> None:
    for violation in parsed.violations:
        violations.append(
            AnkiTemplateViolation(
                violation.code,
                f"{location}@{violation.position}",
                violation.message,
            )
        )


def _verify_side_references(
    parsed: ParsedMustache,
    location: str,
    violations: list[AnkiTemplateViolation],
) -> None:
    allowed_fields = set(PERSISTED_CARD_FIELDS) | set(ANKI_VIRTUAL_FIELDS)
    seen: set[str] = set()
    for field_name in parsed.field_names:
        if field_name in seen:
            continue
        seen.add(field_name)
        if field_name not in allowed_fields:
            violations.append(
                AnkiTemplateViolation(
                    "TEMPLATE_UNKNOWN_FIELD",
                    location,
                    f"unknown persisted or virtual field {field_name!r}",
                )
            )

    for field_name in FORBIDDEN_NORMAL_REVIEW_FIELDS:
        if field_name in seen:
            if field_name in NOVEL_CONTEXT_FIELDS:
                code = "TEMPLATE_NOVEL_CONTEXT_FORBIDDEN"
            elif field_name in RESERVED_AUDIO_FIELDS:
                code = "TEMPLATE_NOVEL_AUDIO_FORBIDDEN"
            else:  # pragma: no cover - human-owned contract exhaustiveness
                raise AssertionError(
                    f"unclassified forbidden review field: {field_name!r}"
                )
            violations.append(
                AnkiTemplateViolation(
                    code,
                    location,
                    f"normal review must not reference {field_name!r}",
                )
            )


def _verify_javascript(
    template: str,
    location: str,
    violations: list[AnkiTemplateViolation],
) -> None:
    if _SCRIPT_TAG_RE.search(template):
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_SCRIPT_TAG_FORBIDDEN",
                location,
                "script tags are forbidden in normal review templates",
            )
        )
    if _INLINE_HANDLER_RE.search(template):
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_INLINE_HANDLER_FORBIDDEN",
                location,
                "inline event-handler attributes are forbidden",
            )
        )
    if _contains_javascript_url(template):
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_JAVASCRIPT_URL_FORBIDDEN",
                location,
                "javascript: URLs are forbidden",
            )
        )


def _contains_javascript_url(template: str) -> bool:
    parser = _JavascriptURLAttributeParser()
    parser.feed(template)
    parser.close()
    return parser.found


def _verify_target_gate(
    template_name: str,
    parsed: ParsedMustache,
    location: str,
    violations: list[AnkiTemplateViolation],
) -> None:
    expected_target = TARGET_FIELD_BY_TEMPLATE_NAME[template_name]
    target_sections = tuple(
        reference
        for reference in parsed.references
        if reference.kind == "section"
        and reference.field_name in TARGET_TEMPLATE_FIELDS
    )
    expected_positive = tuple(
        reference
        for reference in target_sections
        if reference.field_name == expected_target and not reference.inverted
    )
    expected_inverted = tuple(
        reference
        for reference in target_sections
        if reference.field_name == expected_target and reference.inverted
    )
    wrong_targets = tuple(
        reference
        for reference in target_sections
        if reference.field_name != expected_target
    )

    if not expected_positive:
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_TARGET_GATE_MISSING",
                location,
                f"front needs one positive {expected_target!r} section",
            )
        )
    if expected_inverted:
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_TARGET_GATE_INVERTED",
                location,
                f"inverted {expected_target!r} gates are forbidden",
            )
        )
    if wrong_targets:
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_TARGET_GATE_WRONG",
                location,
                "front references another channel gate: "
                f"{tuple(item.field_name for item in wrong_targets)}",
            )
        )
    if len(target_sections) > 1:
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_TARGET_GATE_MULTIPLE",
                location,
                "front must contain exactly one Target_* section",
            )
        )

    # If Mustache nesting is malformed, containment cannot be interpreted
    # safely. The parse violation already fails the snapshot closed.
    if parsed.violations:
        return

    outside_reference = any(
        not _has_positive_ancestor(reference.ancestors, expected_target)
        for reference in parsed.references
        if not (
            reference.kind == "section"
            and reference.field_name == expected_target
            and not reference.inverted
        )
    )
    outside_literal = _outside_literal_can_render(parsed, expected_target)
    if outside_reference or outside_literal:
        violations.append(
            AnkiTemplateViolation(
                "TEMPLATE_CONTENT_OUTSIDE_TARGET_GATE",
                location,
                f"front content must be inside the {expected_target!r} gate",
            )
        )


def _has_positive_ancestor(
    ancestors: tuple[MustacheSection, ...],
    expected_target: str,
) -> bool:
    return any(
        ancestor.field_name == expected_target and not ancestor.inverted
        for ancestor in ancestors
    )


def _outside_literal_can_render(
    parsed: ParsedMustache,
    expected_target: str,
) -> bool:
    outside = "".join(
        fragment.text
        for fragment in parsed.texts
        if not _has_positive_ancestor(fragment.ancestors, expected_target)
    )
    outside = _HTML_COMMENT_RE.sub("", outside)
    outside = _STYLE_BLOCK_RE.sub("", outside)

    content_tag_found = False

    def remove_non_content_tag(match: re.Match[str]) -> str:
        nonlocal content_tag_found
        if match.group(1).lower() in _NON_CONTENT_HTML_TAGS:
            return ""
        content_tag_found = True
        return ""

    outside = _HTML_TAG_RE.sub(remove_non_content_tag, outside)
    if content_tag_found:
        return True
    return bool(html.unescape(outside).strip())


def _verify_requirements(
    raw_requirements: object,
    templates: tuple[_TemplateRecord, ...],
    field_names: tuple[str, ...],
    field_ordinals: dict[str, int],
    violations: list[AnkiTemplateViolation],
) -> None:
    if not isinstance(raw_requirements, list):
        violations.append(
            AnkiTemplateViolation(
                "MODEL_REQ_MALFORMED",
                "model.req",
                "req metadata must be a list",
            )
        )
        return

    template_ordinals = {record.ordinal for record in templates}
    known_field_ordinals = set(field_ordinals.values())
    records_by_template: dict[int, list[tuple[str, tuple[int, ...], int]]] = {}

    for index, branch in enumerate(raw_requirements):
        location = f"model.req[{index}]"
        if not isinstance(branch, (list, tuple)) or len(branch) != 3:
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_MALFORMED",
                    location,
                    "req branch must contain template ordinal, mode, and fields",
                )
            )
            continue

        template_ordinal, mode, raw_field_ordinals = branch
        if (
            type(template_ordinal) is not int
            or template_ordinal not in template_ordinals
            or mode not in {"all", "any", "none"}
            or not isinstance(raw_field_ordinals, (list, tuple))
            or any(type(item) is not int for item in raw_field_ordinals)
        ):
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_MALFORMED",
                    location,
                    "req branch has an unknown template, mode, or field list",
                )
            )
            continue

        ordinals = tuple(raw_field_ordinals)
        if (
            len(set(ordinals)) != len(ordinals)
            or any(ordinal not in known_field_ordinals for ordinal in ordinals)
            or (mode == "none" and ordinals)
            or (mode in {"all", "any"} and not ordinals)
        ):
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_MALFORMED",
                    location,
                    "req field ordinals cannot be interpreted safely",
                )
            )
            continue

        records_by_template.setdefault(template_ordinal, []).append(
            (mode, ordinals, index)
        )

    records_by_name: dict[str, list[_TemplateRecord]] = {}
    for record in templates:
        records_by_name.setdefault(record.name, []).append(record)

    for template_name in CARD_TEMPLATE_NAMES:
        records = records_by_name.get(template_name, [])
        if len(records) != 1:
            continue
        record = records[0]
        requirements = records_by_template.get(record.ordinal, [])
        if not requirements:
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_TEMPLATE_MISSING",
                    f"model.req[{template_name}]",
                    "req metadata has no branch for this card template",
                )
            )
            continue
        if len(requirements) > 1:
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_MALFORMED",
                    f"model.req[{template_name}]",
                    "req metadata must contain exactly one record for this "
                    f"card template, got {len(requirements)}",
                )
            )
            continue

        target_field = TARGET_FIELD_BY_TEMPLATE_NAME[template_name]
        target_ordinal = field_ordinals.get(target_field)
        if target_ordinal is None or target_field not in field_names:
            continue
        mode, ordinals, requirement_index = requirements[0]
        target_is_necessary = (
            mode == "all" and target_ordinal in ordinals
        ) or (
            mode == "any" and ordinals == (target_ordinal,)
        )
        if not target_is_necessary:
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_TARGET_NOT_NECESSARY",
                    f"model.req[{requirement_index}]",
                    f"{target_field!r} is not necessary for card "
                    f"{template_name!r}",
                )
            )

        expected_fields = GENERATION_REQUIREMENTS_BY_TEMPLATE_NAME[
            template_name
        ]
        expected_ordinals = tuple(
            field_ordinals[field_name]
            for field_name in expected_fields
            if field_name in field_ordinals
        )
        if len(expected_ordinals) != len(expected_fields):
            continue

        exact_fields = (
            len(ordinals) == len(expected_ordinals)
            and set(ordinals) == set(expected_ordinals)
        )
        exact_mode = (
            mode == "all"
            if len(expected_ordinals) > 1
            else mode in {"all", "any"}
        )
        if not (exact_fields and exact_mode):
            actual_fields = tuple(
                field_names[ordinal]
                for ordinal in ordinals
                if 0 <= ordinal < len(field_names)
            )
            violations.append(
                AnkiTemplateViolation(
                    "MODEL_REQ_GENERATION_FIELDS_MISMATCH",
                    f"model.req[{requirement_index}]",
                    f"card {template_name!r} must require exactly "
                    f"{expected_fields!r} with semantically equivalent mode; "
                    f"got mode={mode!r}, fields={actual_fields!r}",
                )
            )
