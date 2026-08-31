"""The D71 section 5 Wave C v1 session-plan artifact.

This is the one new operational input schema D71 owns. It is a closed strict
JSON document supplied by the human, who is the session-composition authority.
The parser validates and detaches; it never trims, normalizes, repairs, or
coerces anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..artifact_json import ArtifactJSONError, strict_json_loads
from ..contracts import UNIT_KEY_PATTERN
from .errors import RuntimeSessionPlanError


SESSION_PLAN_ARTIFACT = "vocab.t12.session-plan"
SESSION_PLAN_VERSION = 1

PLAN_KEYS: frozenset[str] = frozenset({"artifact", "v", "items"})

PLAN_ITEM_KEYS_BY_CHANNEL: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "R": frozenset({"unit_key", "channel", "passage", "question"}),
        "W": frozenset(
            {"unit_key", "channel", "production_prompt", "semantic_constraints"}
        ),
    }
)

STIMULUS_FIELDS_BY_CHANNEL: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "R": ("passage", "question"),
        "W": ("production_prompt", "semantic_constraints"),
    }
)

_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


@dataclass(frozen=True, slots=True)
class SessionPlanItem:
    """One validated, detached plan item in the human's supplied order."""

    unit_key: str
    channel: str
    stimulus: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """One validated, detached session plan."""

    items: tuple[SessionPlanItem, ...]


def _require_exact_text(value: object, name: str) -> str:
    """Require an exact non-whitespace str carrying no unpaired surrogate."""
    if type(value) is not str:
        raise RuntimeSessionPlanError(f"{name} must be a string")
    if not value.strip():
        raise RuntimeSessionPlanError(f"{name} must not be blank")
    if _SURROGATE_RE.search(value) is not None:
        raise RuntimeSessionPlanError(f"{name} must not contain a surrogate")
    return value


def _validated_item(raw: object, index: int) -> SessionPlanItem:
    location = f"items[{index}]"
    if not isinstance(raw, dict):
        raise RuntimeSessionPlanError(f"{location} must be a JSON object")

    channel = raw.get("channel")
    if type(channel) is not str or channel not in PLAN_ITEM_KEYS_BY_CHANNEL:
        raise RuntimeSessionPlanError(
            f"{location}.channel must be exactly 'R' or 'W' in Wave C v1"
        )

    expected = PLAN_ITEM_KEYS_BY_CHANNEL[channel]
    present = set(raw)
    unknown = sorted(present - expected)
    if unknown:
        raise RuntimeSessionPlanError(f"{location} has unknown keys: {unknown}")
    missing = sorted(expected - present)
    if missing:
        raise RuntimeSessionPlanError(f"{location} is missing keys: {missing}")

    unit_key = raw["unit_key"]
    if type(unit_key) is not str or _UNIT_KEY_RE.fullmatch(unit_key) is None:
        raise RuntimeSessionPlanError(f"{location}.unit_key is invalid")

    stimulus = {
        field: _require_exact_text(raw[field], f"{location}.{field}")
        for field in STIMULUS_FIELDS_BY_CHANNEL[channel]
    }
    return SessionPlanItem(
        unit_key=unit_key,
        channel=channel,
        stimulus=MappingProxyType(dict(stimulus)),
    )


def validate_session_plan(plan: object) -> SessionPlan:
    """Re-validate any SessionPlan and return a detached snapshot.

    SessionPlan and SessionPlanItem are ordinary dataclasses, so a caller can
    build one without going through parse_session_plan. This is the one pure
    authority both paths end in, so a directly constructed plan cannot bypass
    the D71 section 5 rules, and the returned snapshot cannot be mutated
    afterwards through the caller's own objects.
    """
    if type(plan) is not SessionPlan:
        raise RuntimeSessionPlanError("session plan must be a SessionPlan")
    items = plan.items
    if type(items) is not tuple or not items:
        raise RuntimeSessionPlanError("session plan requires at least one item")

    validated: list[SessionPlanItem] = []
    for index, item in enumerate(items):
        location = f"items[{index}]"
        if type(item) is not SessionPlanItem:
            raise RuntimeSessionPlanError(f"{location} must be a SessionPlanItem")

        channel = item.channel
        if type(channel) is not str or channel not in PLAN_ITEM_KEYS_BY_CHANNEL:
            raise RuntimeSessionPlanError(
                f"{location}.channel must be exactly 'R' or 'W' in Wave C v1"
            )

        unit_key = item.unit_key
        if type(unit_key) is not str or _UNIT_KEY_RE.fullmatch(unit_key) is None:
            raise RuntimeSessionPlanError(f"{location}.unit_key is invalid")

        stimulus = item.stimulus
        if not isinstance(stimulus, Mapping):
            raise RuntimeSessionPlanError(f"{location}.stimulus must be a mapping")
        expected = set(STIMULUS_FIELDS_BY_CHANNEL[channel])
        if set(stimulus) != expected:
            raise RuntimeSessionPlanError(
                f"{location}.stimulus must carry exactly {sorted(expected)}"
            )
        detached = {
            field: _require_exact_text(stimulus[field], f"{location}.{field}")
            for field in STIMULUS_FIELDS_BY_CHANNEL[channel]
        }
        validated.append(
            SessionPlanItem(
                unit_key=unit_key,
                channel=channel,
                stimulus=MappingProxyType(detached),
            )
        )
    return SessionPlan(items=tuple(validated))


def parse_session_plan(raw_bytes: object) -> SessionPlan:
    """Strict-decode and fully validate one session-plan artifact.

    The returned plan is detached: mutating the caller's original object or the
    source file afterwards cannot change what was validated.
    """
    if type(raw_bytes) is not bytes:
        raise RuntimeSessionPlanError("session plan must be exact bytes")
    try:
        decoded = strict_json_loads(raw_bytes)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeSessionPlanError(
            f"session plan is not strict JSON: {exc}"
        ) from exc

    if not isinstance(decoded, dict):
        raise RuntimeSessionPlanError("session plan must be a JSON object")
    present = set(decoded)
    unknown = sorted(present - PLAN_KEYS)
    if unknown:
        raise RuntimeSessionPlanError(f"session plan has unknown keys: {unknown}")
    missing = sorted(PLAN_KEYS - present)
    if missing:
        raise RuntimeSessionPlanError(f"session plan is missing keys: {missing}")

    if decoded["artifact"] != SESSION_PLAN_ARTIFACT:
        raise RuntimeSessionPlanError(
            f"session plan artifact must be {SESSION_PLAN_ARTIFACT!r}"
        )

    version = decoded["v"]
    if type(version) is not int or version != SESSION_PLAN_VERSION:
        raise RuntimeSessionPlanError(
            f"session plan v must be exactly {SESSION_PLAN_VERSION}"
        )

    items = decoded["items"]
    if not isinstance(items, list):
        raise RuntimeSessionPlanError("session plan items must be a list")
    if not items:
        raise RuntimeSessionPlanError("session plan requires at least one item")

    return validate_session_plan(
        SessionPlan(
            items=tuple(
                _validated_item(item, index) for index, item in enumerate(items)
            )
        )
    )
