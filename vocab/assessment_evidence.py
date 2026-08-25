"""Read-only D63 attempt evidence and detached Unit snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .artifact_store import ArtifactStore
from .assessment_identity import assessment_attempt_id
from .capture_ledger import read_capture_ledger, validate_capture_bindings
from .contracts import (
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    CHANNELS,
    TARGET_FIELD_BY_CHANNEL,
    TARGET_FLAG_VALUE,
)
from .exposure import novelty_for_reserved_attempt, read_exposure_ledger
from .models import VocabUnit
from .session import load_session_manifest
from .validators import validate_forge_unit


class AssessmentEvidenceError(ValueError):
    """Raised when durable T12 or Unit evidence cannot be trusted."""


_EVIDENCE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class ValidatedUnitEvidence:
    """A detached immutable snapshot of one validated runtime Unit."""

    unit_key: str
    lemma: str
    unit_type: str
    definition_en: str
    enabled_channels: tuple[str, ...]
    _seal: object = field(repr=False, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> ValidatedUnitEvidence:
        raise TypeError(
            "ValidatedUnitEvidence can only be issued by validate_unit_evidence"
        )


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAttemptEvidence:
    """One captured text attempt reconstructed from durable T12 authorities."""

    attempt_id: str
    session_id: str
    item_ordinal: int
    unit_key: str
    channel: str
    task_kind: str
    presented_stimulus_ref: str
    stimulus_artifact_ref: str
    response_artifact_ref: str
    novel: bool
    response_bytes: bytes = field(repr=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(
        cls,
        *_args: object,
        **_kwargs: object,
    ) -> ValidatedAttemptEvidence:
        raise TypeError(
            "ValidatedAttemptEvidence can only be issued by "
            "load_validated_attempt_evidence"
        )


def validate_unit_evidence(unit: VocabUnit) -> ValidatedUnitEvidence:
    """Validate the existing Unit boundary and return a detached snapshot."""
    if not isinstance(unit, VocabUnit):
        raise TypeError("unit must be a VocabUnit")
    violations = validate_forge_unit(unit)
    if violations:
        raise AssessmentEvidenceError(
            "Unit does not pass the existing validation boundary: "
            + ", ".join(violations)
        )
    enabled_channels = tuple(
        channel
        for channel in CHANNELS
        if getattr(unit, TARGET_FIELD_BY_CHANNEL[channel]) == TARGET_FLAG_VALUE
    )
    return _issue_unit_evidence(
        unit_key=unit.unit_key,
        lemma=unit.lemma,
        unit_type=unit.unit_type,
        definition_en=unit.definition_en,
        enabled_channels=enabled_channels,
    )


def load_validated_attempt_evidence(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    session_root: str | os.PathLike[str],
    attempt_id: object,
) -> ValidatedAttemptEvidence:
    """Reconstruct one captured attempt from complete durable T12 history."""
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")

    exposures = read_exposure_ledger(exposure_path)
    captures = read_capture_ledger(capture_path)
    validate_capture_bindings(
        captures,
        exposure_attempt_ids=tuple(item.attempt_id for item in exposures),
        artifact_store=artifact_store,
    )

    if type(attempt_id) is not str:
        raise AssessmentEvidenceError("attempt_id selector must be a string")
    matching_exposures = [
        item for item in exposures if item.attempt_id == attempt_id
    ]
    if len(matching_exposures) != 1:
        raise AssessmentEvidenceError(
            "attempt evidence requires exactly one exposure reservation"
        )
    reservation = matching_exposures[0]

    manifest = load_session_manifest(session_root, reservation.session_id)
    manifest_data = manifest.to_dict()
    items = manifest_data["items"]
    if type(items) is not list:  # pragma: no cover - manifest import guarantees it
        raise AssertionError("validated session items are not an array")
    matching_items = [
        item
        for item in items
        if type(item) is dict
        and item.get("item_ordinal") == reservation.item_ordinal
    ]
    if len(matching_items) != 1:
        raise AssessmentEvidenceError(
            "persisted session does not contain exactly one reserved item"
        )
    item = matching_items[0]
    expected_item_binding = (
        manifest.session_id,
        item["item_ordinal"],
        item["unit_key"],
        item["channel"],
        item["presented_stimulus_ref"],
        item["stimulus_artifact_ref"],
    )
    reservation_binding = (
        reservation.session_id,
        reservation.item_ordinal,
        reservation.unit_key,
        reservation.channel,
        reservation.presented_stimulus_ref,
        reservation.stimulus_artifact_ref,
    )
    if expected_item_binding != reservation_binding:
        raise AssessmentEvidenceError(
            "exposure reservation does not match the persisted session item"
        )

    expected_attempt_id = assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=reservation.item_ordinal,
        unit_key=reservation.unit_key,
        channel=reservation.channel,
        presented_stimulus_ref=reservation.presented_stimulus_ref,
    )
    if expected_attempt_id != reservation.attempt_id:
        raise AssessmentEvidenceError(
            "attempt identity does not match the persisted session item"
        )

    artifact_store.read(reservation.stimulus_artifact_ref)

    matching_captures = [
        receipt for receipt in captures if receipt.attempt_id == reservation.attempt_id
    ]
    if len(matching_captures) != 1:
        raise AssessmentEvidenceError(
            "captured text assessment requires exactly one capture receipt"
        )
    receipt = matching_captures[0]
    response_bytes = artifact_store.read(receipt.response_artifact_ref)
    novel = novelty_for_reserved_attempt(exposure_path, reservation.attempt_id)

    return _issue_attempt_evidence(
        attempt_id=reservation.attempt_id,
        session_id=reservation.session_id,
        item_ordinal=reservation.item_ordinal,
        unit_key=reservation.unit_key,
        channel=reservation.channel,
        task_kind=ASSESSMENT_TASK_KIND_BY_CHANNEL[reservation.channel],
        presented_stimulus_ref=reservation.presented_stimulus_ref,
        stimulus_artifact_ref=reservation.stimulus_artifact_ref,
        response_artifact_ref=receipt.response_artifact_ref,
        novel=novel,
        response_bytes=response_bytes,
    )


def _issue_unit_evidence(
    *,
    unit_key: str,
    lemma: str,
    unit_type: str,
    definition_en: str,
    enabled_channels: tuple[str, ...],
) -> ValidatedUnitEvidence:
    evidence = object.__new__(ValidatedUnitEvidence)
    object.__setattr__(evidence, "unit_key", unit_key)
    object.__setattr__(evidence, "lemma", lemma)
    object.__setattr__(evidence, "unit_type", unit_type)
    object.__setattr__(evidence, "definition_en", definition_en)
    object.__setattr__(evidence, "enabled_channels", tuple(enabled_channels))
    object.__setattr__(evidence, "_seal", _EVIDENCE_SEAL)
    return evidence


def _issue_attempt_evidence(
    *,
    attempt_id: str,
    session_id: str,
    item_ordinal: int,
    unit_key: str,
    channel: str,
    task_kind: str,
    presented_stimulus_ref: str,
    stimulus_artifact_ref: str,
    response_artifact_ref: str,
    novel: bool,
    response_bytes: bytes,
) -> ValidatedAttemptEvidence:
    evidence = object.__new__(ValidatedAttemptEvidence)
    object.__setattr__(evidence, "attempt_id", attempt_id)
    object.__setattr__(evidence, "session_id", session_id)
    object.__setattr__(evidence, "item_ordinal", item_ordinal)
    object.__setattr__(evidence, "unit_key", unit_key)
    object.__setattr__(evidence, "channel", channel)
    object.__setattr__(evidence, "task_kind", task_kind)
    object.__setattr__(
        evidence,
        "presented_stimulus_ref",
        presented_stimulus_ref,
    )
    object.__setattr__(evidence, "stimulus_artifact_ref", stimulus_artifact_ref)
    object.__setattr__(evidence, "response_artifact_ref", response_artifact_ref)
    object.__setattr__(evidence, "novel", novel)
    object.__setattr__(evidence, "response_bytes", bytes(response_bytes))
    object.__setattr__(evidence, "_seal", _EVIDENCE_SEAL)
    return evidence


def _require_unit_evidence(value: object) -> ValidatedUnitEvidence:
    if type(value) is not ValidatedUnitEvidence:
        raise TypeError("unit must be a ValidatedUnitEvidence")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError("unit evidence was not issued by validate_unit_evidence") from None
    if seal is not _EVIDENCE_SEAL:
        raise TypeError("unit evidence was not issued by validate_unit_evidence")
    if value.enabled_channels != tuple(
        channel for channel in CHANNELS if channel in value.enabled_channels
    ):
        raise AssessmentEvidenceError("unit evidence enabled_channels is incoherent")
    return value


def _require_attempt_evidence(value: object) -> ValidatedAttemptEvidence:
    if type(value) is not ValidatedAttemptEvidence:
        raise TypeError("attempt must be a ValidatedAttemptEvidence")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError(
            "attempt evidence was not issued by load_validated_attempt_evidence"
        ) from None
    if seal is not _EVIDENCE_SEAL:
        raise TypeError(
            "attempt evidence was not issued by load_validated_attempt_evidence"
        )
    if type(value.response_bytes) is not bytes or type(value.novel) is not bool:
        raise AssessmentEvidenceError("attempt evidence runtime fields are incoherent")
    if value.task_kind != ASSESSMENT_TASK_KIND_BY_CHANNEL.get(value.channel):
        raise AssessmentEvidenceError("attempt channel/task_kind binding is incoherent")
    return value


def _unit_binding(value: ValidatedUnitEvidence) -> tuple[object, ...]:
    unit = _require_unit_evidence(value)
    return (
        unit.unit_key,
        unit.lemma,
        unit.unit_type,
        unit.definition_en,
        unit.enabled_channels,
    )


def _require_attempt_unit_binding(
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
) -> None:
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    if validated_attempt.unit_key != validated_unit.unit_key:
        raise AssessmentEvidenceError("attempt and Unit unit_key do not match")
    if validated_attempt.channel not in validated_unit.enabled_channels:
        raise AssessmentEvidenceError("attempt channel is not enabled in the Unit")
