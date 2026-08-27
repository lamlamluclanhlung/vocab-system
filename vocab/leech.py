"""Pure verification of Anki leech deck-option configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .contracts import ANKI_LEECH_ACTION, ANKI_LEECH_THRESHOLD


@dataclass(frozen=True, slots=True)
class LeechConfigViolation:
    """One deterministic local T7 configuration diagnostic."""

    code: str
    message: str


# AnkiConnect wire values are local transport details, not domain contracts.
_LEECH_ACTION_WIRE_BY_NAME: Final[dict[str, int]] = {
    "suspend_card": 0,
    "tag_only": 1,
}
_SUSPEND_CARD_WIRE_ACTION: Final[int] = _LEECH_ACTION_WIRE_BY_NAME[
    "suspend_card"
]
_EXPECTED_LEECH_ACTION: Final[int] = _LEECH_ACTION_WIRE_BY_NAME[
    ANKI_LEECH_ACTION
]


def verify_leech_config(
    config: object,
) -> tuple[LeechConfigViolation, ...]:
    """Return all deterministic T7 violations without mutating ``config``."""
    if not isinstance(config, Mapping):
        return (
            LeechConfigViolation(
                "L7_CONFIG_SHAPE_INVALID",
                "deck configuration must be a mapping",
            ),
        )

    lapse = config.get("lapse")
    if not isinstance(lapse, Mapping):
        return (
            LeechConfigViolation(
                "L7_LAPSE_SECTION_MISSING",
                "deck configuration must contain a lapse mapping",
            ),
        )

    violations: list[LeechConfigViolation] = []
    threshold = lapse.get("leechFails")
    if type(threshold) is not int or threshold != ANKI_LEECH_THRESHOLD:
        violations.append(
            LeechConfigViolation(
                "L7_THRESHOLD_MISMATCH",
                f"leechFails must be integer {ANKI_LEECH_THRESHOLD}",
            )
        )

    action = lapse.get("leechAction")
    if type(action) is not int:
        violations.append(
            LeechConfigViolation(
                "L7_ACTION_UNKNOWN",
                "leechAction must be the Tag Only wire integer",
            )
        )
    elif action == _SUSPEND_CARD_WIRE_ACTION:
        violations.append(
            LeechConfigViolation(
                "L7_ACTION_SUSPEND",
                "leechAction is Suspend Card; Tag Only is required",
            )
        )
    elif action != _EXPECTED_LEECH_ACTION:
        violations.append(
            LeechConfigViolation(
                "L7_ACTION_UNKNOWN",
                f"unknown leechAction wire value {action!r}",
            )
        )

    return tuple(violations)
