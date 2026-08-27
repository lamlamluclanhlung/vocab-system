"""Pure T12 cognitive-stimulus and assessment-attempt identities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping

from .artifact_json import canonical_json_bytes
from .contracts import (
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    COGNITIVE_STIMULUS_NORMALIZATION_FORM,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)


COGNITIVE_STIMULUS_DOMAIN = "vocab.t12.cognitive-stimulus"
COGNITIVE_STIMULUS_VERSION = 1
ATTEMPT_DOMAIN = "vocab.t12.attempt"
ATTEMPT_VERSION = 1
SESSION_ID_PATTERN = r"^session:v1:[0-9a-f]{64}$"

_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_STIMULUS_REF_RE = re.compile(ASSESSMENT_STIMULUS_REF_PATTERN)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_UNICODE_WHITESPACE_RE = re.compile(r"\s+", flags=re.UNICODE)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_STIMULUS_FIELDS_BY_CHANNEL = {
    "R": ("passage", "question"),
    "L": ("spoken_script", "question"),
    "W": ("production_prompt", "semantic_constraints"),
    "S": ("production_prompt", "semantic_constraints"),
}
_PROJECTION_FIELDS_BY_CHANNEL = {
    "R": ("canonical_passage", "canonical_question"),
    "L": ("canonical_spoken_script", "canonical_question"),
    "W": ("canonical_production_prompt", "canonical_semantic_constraints"),
    "S": ("canonical_production_prompt", "canonical_semantic_constraints"),
}


class AssessmentIdentityError(ValueError):
    """Raised when a T12 identity input is not exact and closed."""


def normalize_cognitive_text(value: object) -> str:
    """Return the D54 cognitive normalization of one stimulus string."""
    if type(value) is not str:
        raise AssessmentIdentityError("cognitive stimulus text must be a string")
    if _SURROGATE_RE.search(value) is not None:
        raise AssessmentIdentityError(
            "cognitive stimulus text contains an unpaired surrogate"
        )
    normalized = unicodedata.normalize(
        COGNITIVE_STIMULUS_NORMALIZATION_FORM,
        value,
    )
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _UNICODE_WHITESPACE_RE.sub(" ", normalized).strip()
    if not normalized:
        raise AssessmentIdentityError(
            "cognitive stimulus text must be non-whitespace"
        )
    return normalized


def cognitive_stimulus_projection(
    *,
    unit_key: object,
    channel: object,
    task_kind: object,
    stimulus: object,
) -> dict[str, object]:
    """Build the exact closed D54 cognitive-stimulus projection."""
    validated_unit_key = _validated_unit_key(unit_key)
    validated_channel, validated_task_kind = _validated_channel_task(
        channel,
        task_kind,
    )
    if not isinstance(stimulus, Mapping):
        raise AssessmentIdentityError("stimulus must be an object")

    source_fields = _STIMULUS_FIELDS_BY_CHANNEL[validated_channel]
    if set(stimulus) != set(source_fields):
        raise AssessmentIdentityError(
            f"stimulus has the wrong key set for channel {validated_channel}"
        )
    projection_fields = _PROJECTION_FIELDS_BY_CHANNEL[validated_channel]
    projection: dict[str, object] = {
        "domain": COGNITIVE_STIMULUS_DOMAIN,
        "v": COGNITIVE_STIMULUS_VERSION,
        "unit_key": validated_unit_key,
        "channel": validated_channel,
        "task_kind": validated_task_kind,
    }
    for source_field, projection_field in zip(
        source_fields,
        projection_fields,
        strict=True,
    ):
        projection[projection_field] = normalize_cognitive_text(
            stimulus[source_field]
        )
    return projection


def cognitive_stimulus_ref(
    *,
    unit_key: object,
    channel: object,
    task_kind: object,
    stimulus: object,
) -> str:
    """Return ``stimulus:v1:<sha256>`` for the D54 projection."""
    projection = cognitive_stimulus_projection(
        unit_key=unit_key,
        channel=channel,
        task_kind=task_kind,
        stimulus=stimulus,
    )
    digest = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    result = f"stimulus:v1:{digest}"
    if _STIMULUS_REF_RE.fullmatch(result) is None:  # pragma: no cover
        raise AssertionError("derived stimulus ref violated its frozen grammar")
    return result


def attempt_projection(
    *,
    session_id: object,
    item_ordinal: object,
    unit_key: object,
    channel: object,
    presented_stimulus_ref: object,
) -> dict[str, object]:
    """Build the exact closed D54 assessment-attempt projection."""
    if type(session_id) is not str or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise AssessmentIdentityError("session_id is invalid")
    if type(item_ordinal) is not int or item_ordinal < 0:
        raise AssessmentIdentityError(
            "item_ordinal must be an actual non-negative integer"
        )
    validated_unit_key = _validated_unit_key(unit_key)
    if type(channel) is not str or channel not in ASSESSMENT_TASK_KIND_BY_CHANNEL:
        raise AssessmentIdentityError("channel is invalid")
    if (
        type(presented_stimulus_ref) is not str
        or _STIMULUS_REF_RE.fullmatch(presented_stimulus_ref) is None
    ):
        raise AssessmentIdentityError("presented_stimulus_ref is invalid")
    return {
        "domain": ATTEMPT_DOMAIN,
        "v": ATTEMPT_VERSION,
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "session_id": session_id,
        "item_ordinal": item_ordinal,
        "unit_key": validated_unit_key,
        "channel": channel,
        "presented_stimulus_ref": presented_stimulus_ref,
    }


def assessment_attempt_id(
    *,
    session_id: object,
    item_ordinal: object,
    unit_key: object,
    channel: object,
    presented_stimulus_ref: object,
) -> str:
    """Return ``attempt:v1:<sha256>`` for the D54 attempt projection."""
    projection = attempt_projection(
        session_id=session_id,
        item_ordinal=item_ordinal,
        unit_key=unit_key,
        channel=channel,
        presented_stimulus_ref=presented_stimulus_ref,
    )
    digest = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    result = f"attempt:v1:{digest}"
    if _ATTEMPT_ID_RE.fullmatch(result) is None:  # pragma: no cover
        raise AssertionError("derived attempt id violated its frozen grammar")
    return result


def _validated_unit_key(value: object) -> str:
    if type(value) is not str or _UNIT_KEY_RE.fullmatch(value) is None:
        raise AssessmentIdentityError("unit_key is invalid")
    return value


def _validated_channel_task(
    channel: object,
    task_kind: object,
) -> tuple[str, str]:
    if type(channel) is not str or channel not in ASSESSMENT_TASK_KIND_BY_CHANNEL:
        raise AssessmentIdentityError("channel is invalid")
    expected_task_kind = ASSESSMENT_TASK_KIND_BY_CHANNEL[channel]
    if type(task_kind) is not str or task_kind != expected_task_kind:
        raise AssessmentIdentityError("task_kind does not match channel")
    return channel, task_kind
