"""D68 T12 EventLog producer with strict preflight and crash recovery."""

from __future__ import annotations

import os
import warnings
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .artifact_json import canonical_json_bytes
from .artifact_store import ArtifactStore
from .assessment_planning import (
    PlannedJudge,
    _require_planned_judge,
    _validated_judge_payload,
)
from .capture_ledger import CaptureReceipt
from .contracts import (
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_PASS,
    EVENT_SCHEMA_VERSION,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from .disposition_ledger import (
    DISPOSITION_CODES,
    OperationalDispositionReceipt,
)
from .events import EventLog, EventLogCorruptionWarning
from .exposure import (
    ExposureReservation,
    novelty_for_reserved_attempt_history,
    validate_t12_histories,
)
from .models import Event
from .speech_planning import (
    PlannedSpeechAssessment,
    _require_planned_speech_assessment,
    _validate_companion_pair,
    _validated_speak_payload,
    _validated_speech_judge_payload,
)


T12_PRODUCER_EVENT_SCHEMA_VERSION = 1

__all__ = (
    "emit_planned_judge",
    "emit_planned_speech_assessment",
)

_T12_EVENT_TYPES = frozenset(("JUDGE", "SPEAK"))


class AssessmentProducerError(ValueError):
    """Raised when T12 EventLog emission cannot be proved safe."""


class AssessmentProducerHistoryError(AssessmentProducerError):
    """Raised when EventLog history is corrupt or conflicts with authority."""


class AssessmentProducerAppendError(AssessmentProducerError):
    """Raised when one durable EventLog append does not return normally."""


@dataclass(frozen=True, slots=True)
class _DurableSnapshot:
    exposures: tuple[ExposureReservation, ...]
    captures: tuple[CaptureReceipt, ...]
    dispositions: tuple[OperationalDispositionReceipt, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedT12Event:
    event: Event
    payload: dict[str, object]
    canonical_payload_bytes: bytes

    @property
    def slot(self) -> tuple[str, int, str, str]:
        return (
            T12_ASSESSMENT_PRODUCER_ID,
            T12_ASSESSMENT_PRODUCER_VERSION,
            self.event.event,
            self.payload["attempt_id"],
        )


def emit_planned_judge(
    *,
    event_log: EventLog,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    planned: PlannedJudge,
) -> tuple[Event, ...]:
    """Append one missing exact R/L/W JUDGE, or return an exact rerun."""
    _entry_gate(
        event_log=event_log,
        artifact_store=artifact_store,
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
    )
    payload = _require_planned_judge(planned)
    snapshot = _load_snapshot(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
    )
    _validate_durable_correspondence(
        event_type="JUDGE",
        unit_key=planned.unit_key,
        payload=payload,
        snapshot=snapshot,
    )
    planned_bytes = canonical_json_bytes(payload)
    history = _strict_read_event_history(event_log)
    validated, index = _validated_history(history, snapshot=snapshot)
    classification = _classify_slot(
        index=index,
        event_type="JUDGE",
        attempt_id=payload["attempt_id"],
        unit_key=planned.unit_key,
        canonical_payload_bytes=planned_bytes,
    )
    if classification == "exact":
        return ()
    if classification == "conflicting":
        raise AssessmentProducerHistoryError(
            "planned JUDGE conflicts with the existing T12 slot"
        )
    del validated
    return (_append_judge(event_log, planned.unit_key, payload),)


def emit_planned_speech_assessment(
    *,
    event_log: EventLog,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
    planned: PlannedSpeechAssessment,
) -> tuple[Event, ...]:
    """Idempotently append one atomic planned SPEAK/JUDGE evidence pair."""
    _entry_gate(
        event_log=event_log,
        artifact_store=artifact_store,
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
    )
    speak, judge = _require_planned_speech_assessment(planned)
    snapshot = _load_snapshot(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
    )
    _validate_durable_correspondence(
        event_type="SPEAK",
        unit_key=planned.unit_key,
        payload=speak,
        snapshot=snapshot,
    )
    _validate_durable_correspondence(
        event_type="JUDGE",
        unit_key=planned.unit_key,
        payload=judge,
        snapshot=snapshot,
    )
    _validate_companion_pair(
        unit_key=planned.unit_key,
        response_audio_ref=planned.response_audio_ref,
        speak=speak,
        judge=judge,
    )
    speak_bytes = canonical_json_bytes(speak)
    judge_bytes = canonical_json_bytes(judge)
    history = _strict_read_event_history(event_log)
    _validated, index = _validated_history(history, snapshot=snapshot)
    speak_state = _classify_slot(
        index=index,
        event_type="SPEAK",
        attempt_id=planned.attempt_id,
        unit_key=planned.unit_key,
        canonical_payload_bytes=speak_bytes,
    )
    judge_state = _classify_slot(
        index=index,
        event_type="JUDGE",
        attempt_id=planned.attempt_id,
        unit_key=planned.unit_key,
        canonical_payload_bytes=judge_bytes,
    )
    if speak_state == "conflicting" or judge_state == "conflicting":
        raise AssessmentProducerHistoryError(
            "planned speech assessment conflicts with an existing T12 slot"
        )
    if speak_state == "missing" and judge_state == "exact":
        raise AssessmentProducerHistoryError(
            "T12 speech JUDGE exists without its companion SPEAK"
        )
    if speak_state == "exact" and judge_state == "exact":
        return ()
    if speak_state == "exact":
        return (_append_judge(event_log, planned.unit_key, judge),)

    stored_speak = _append_speak(event_log, planned.unit_key, speak)
    _confirm_speak_append(
        event_log=event_log,
        snapshot=snapshot,
        unit_key=planned.unit_key,
        attempt_id=planned.attempt_id,
        canonical_payload_bytes=speak_bytes,
    )
    stored_judge = _append_judge(event_log, planned.unit_key, judge)
    return stored_speak, stored_judge


def _entry_gate(
    *,
    event_log: object,
    artifact_store: object,
    exposure_path: object,
    capture_path: object,
    disposition_path: object,
) -> None:
    if type(event_log) is not EventLog:
        raise TypeError("event_log must be exactly an EventLog")
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    for name, path in (
        ("exposure", exposure_path),
        ("capture", capture_path),
        ("disposition", disposition_path),
    ):
        if path is None:
            raise TypeError(f"{name} ledger path must be explicit")
        try:
            ledger_path = Path(path)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} ledger path must be path-like") from exc
        if not ledger_path.name:
            raise AssessmentProducerError(
                f"{name} ledger path must identify a file"
            )
    if not (
        EVENT_SCHEMA_VERSION
        == T12_PRODUCER_EVENT_SCHEMA_VERSION
        == 1
    ):
        raise AssessmentProducerError(
            "T12 producer EventLog schema authority is not version 1"
        )


def _load_snapshot(
    *,
    exposure_path: str | os.PathLike[str],
    capture_path: str | os.PathLike[str],
    disposition_path: str | os.PathLike[str],
    artifact_store: ArtifactStore,
) -> _DurableSnapshot:
    exposures, captures, dispositions = validate_t12_histories(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=artifact_store,
    )
    return _DurableSnapshot(exposures, captures, dispositions)


def _strict_read_event_history(event_log: EventLog) -> list[Event]:
    """Decode one complete newline-terminated EventLog without tolerance."""
    try:
        raw = event_log.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise AssessmentProducerHistoryError(
                "EventLog final record is not newline-terminated"
            )
        with warnings.catch_warnings():
            warnings.simplefilter("error", EventLogCorruptionWarning)
            history = event_log.read()
    except AssessmentProducerHistoryError:
        raise
    except Exception as exc:
        raise AssessmentProducerHistoryError(
            "strict EventLog history read failed"
        ) from exc
    if type(history) is not list or any(type(item) is not Event for item in history):
        raise AssessmentProducerHistoryError(
            "strict EventLog read did not return exact Event values"
        )
    return history


def _validated_history(
    history: Sequence[Event],
    *,
    snapshot: _DurableSnapshot,
) -> tuple[
    tuple[_ValidatedT12Event, ...],
    dict[tuple[str, int, str, str], tuple[_ValidatedT12Event, ...]],
]:
    partition = _partition_t12_history(history)
    validated = tuple(_validate_historical_t12_event(item) for item in partition)
    mutable_index: dict[
        tuple[str, int, str, str], list[_ValidatedT12Event]
    ] = defaultdict(list)
    for item in validated:
        mutable_index[item.slot].append(item)
    duplicates = [slot for slot, records in mutable_index.items() if len(records) > 1]
    if duplicates:
        raise AssessmentProducerHistoryError(
            f"duplicate physical T12 EventLog slot exists: {duplicates[0]!r}"
        )
    for item in validated:
        _validate_durable_correspondence(
            event_type=item.event.event,
            unit_key=item.event.unit_key,
            payload=item.payload,
            snapshot=snapshot,
        )
    _validate_historical_speech_companions(validated)
    return validated, {
        slot: tuple(records) for slot, records in mutable_index.items()
    }


def _partition_t12_history(history: Sequence[Event]) -> tuple[Event, ...]:
    result: list[Event] = []
    for event in history:
        payload = event.payload
        if (
            event.event in _T12_EVENT_TYPES
            and type(payload) is dict
            and payload.get("producer") == T12_ASSESSMENT_PRODUCER_ID
        ):
            if (
                type(event.v) is not int
                or event.v != T12_PRODUCER_EVENT_SCHEMA_VERSION
                or type(payload.get("producer_version")) is not int
                or payload.get("producer_version")
                != T12_ASSESSMENT_PRODUCER_VERSION
            ):
                raise AssessmentProducerHistoryError(
                    "T12 EventLog record has an unsupported envelope or producer version"
                )
            result.append(event)
    return tuple(result)


def _validate_historical_t12_event(event: Event) -> _ValidatedT12Event:
    try:
        if event.event == "SPEAK":
            payload = _validated_speak_payload(
                unit_key=event.unit_key,
                payload=event.payload,
            )
        elif event.payload.get("channel") == "S":
            payload = _validated_speech_judge_payload(
                unit_key=event.unit_key,
                payload=event.payload,
            )
        else:
            payload = _validated_judge_payload(
                unit_key=event.unit_key,
                payload=event.payload,
            )
        canonical = canonical_json_bytes(payload)
    except Exception as exc:
        raise AssessmentProducerHistoryError(
            "historical T12 producer payload is invalid"
        ) from exc
    return _ValidatedT12Event(event, payload, canonical)


def _validate_durable_correspondence(
    *,
    event_type: str,
    unit_key: str,
    payload: Mapping[str, object],
    snapshot: _DurableSnapshot,
) -> None:
    attempt_id = payload["attempt_id"]
    exposures = tuple(
        item for item in snapshot.exposures if item.attempt_id == attempt_id
    )
    if len(exposures) != 1:
        raise AssessmentProducerHistoryError(
            "T12 event does not bind exactly one exposure reservation"
        )
    exposure = exposures[0]
    if (
        unit_key != exposure.unit_key
        or payload["channel"] != exposure.channel
        or payload["presented_stimulus_ref"]
        != exposure.presented_stimulus_ref
    ):
        raise AssessmentProducerHistoryError(
            "T12 event conflicts with its exposure reservation"
        )

    captures = tuple(
        item for item in snapshot.captures if item.attempt_id == attempt_id
    )
    dispositions = tuple(
        item for item in snapshot.dispositions if item.attempt_id == attempt_id
    )
    channel = payload["channel"]
    if event_type == "SPEAK" or channel == "S":
        response_field = (
            "response_audio_ref" if event_type == "SPEAK" else "response_artifact_ref"
        )
        if (
            channel != "S"
            or len(captures) != 1
            or dispositions
            or payload.get(response_field) != captures[0].response_artifact_ref
        ):
            raise AssessmentProducerHistoryError(
                "T12 speech event conflicts with capture authority"
            )
    elif event_type == "JUDGE" and channel in ("R", "L", "W"):
        if "response_artifact_ref" in payload:
            if (
                len(captures) != 1
                or dispositions
                or payload["response_artifact_ref"]
                != captures[0].response_artifact_ref
            ):
                raise AssessmentProducerHistoryError(
                    "captured-text JUDGE conflicts with capture authority"
                )
        elif (
            payload.get("outcome") != ASSESSMENT_OUTCOME_ABSTAIN
            or payload.get("reason_code") not in DISPOSITION_CODES
            or captures
            or len(dispositions) != 1
            or payload.get("reason_code") != dispositions[0].disposition_code
        ):
            raise AssessmentProducerHistoryError(
                "policy JUDGE conflicts with disposition authority"
            )
    else:
        raise AssessmentProducerHistoryError("T12 event has an illegal path shape")

    if event_type == "JUDGE" and payload.get("outcome") in (
        ASSESSMENT_OUTCOME_PASS,
        ASSESSMENT_OUTCOME_FAIL,
    ):
        novelty = novelty_for_reserved_attempt_history(
            snapshot.exposures,
            attempt_id,
        )
        if (
            payload.get("assessment_id") != attempt_id
            or payload.get("stimulus_ref") != payload["presented_stimulus_ref"]
            or payload.get("novel") is not novelty
        ):
            raise AssessmentProducerHistoryError(
                "lifecycle JUDGE conflicts with D35 durable authority"
            )


def _validate_historical_speech_companions(
    history: Sequence[_ValidatedT12Event],
) -> None:
    by_attempt: dict[str, dict[str, _ValidatedT12Event]] = defaultdict(dict)
    for item in history:
        if item.event.event == "SPEAK" or item.payload["channel"] == "S":
            by_attempt[item.payload["attempt_id"]][item.event.event] = item
    for attempt_id, pair in by_attempt.items():
        speak_item = pair.get("SPEAK")
        judge_item = pair.get("JUDGE")
        if speak_item is None and judge_item is not None:
            raise AssessmentProducerHistoryError(
                f"speech JUDGE has no companion SPEAK for {attempt_id}"
            )
        if speak_item is not None and judge_item is not None:
            try:
                _validate_companion_pair(
                    unit_key=speak_item.event.unit_key,
                    response_audio_ref=speak_item.payload["response_audio_ref"],
                    speak=speak_item.payload,
                    judge=judge_item.payload,
                )
            except Exception as exc:
                raise AssessmentProducerHistoryError(
                    f"historical speech companions conflict for {attempt_id}"
                ) from exc
            if speak_item.event.unit_key != judge_item.event.unit_key:
                raise AssessmentProducerHistoryError(
                    f"historical speech companions disagree on unit_key for {attempt_id}"
                )


def _classify_slot(
    *,
    index: Mapping[
        tuple[str, int, str, str], tuple[_ValidatedT12Event, ...]
    ],
    event_type: str,
    attempt_id: object,
    unit_key: str,
    canonical_payload_bytes: bytes,
) -> str:
    slot = (
        T12_ASSESSMENT_PRODUCER_ID,
        T12_ASSESSMENT_PRODUCER_VERSION,
        event_type,
        attempt_id,
    )
    records = index.get(slot, ())
    if not records:
        return "missing"
    record = records[0]
    if (
        record.event.unit_key == unit_key
        and record.canonical_payload_bytes == canonical_payload_bytes
    ):
        return "exact"
    return "conflicting"


def _confirm_speak_append(
    *,
    event_log: EventLog,
    snapshot: _DurableSnapshot,
    unit_key: str,
    attempt_id: str,
    canonical_payload_bytes: bytes,
) -> None:
    history = _strict_read_event_history(event_log)
    validated, index = _validated_history(history, snapshot=snapshot)
    speak_state = _classify_slot(
        index=index,
        event_type="SPEAK",
        attempt_id=attempt_id,
        unit_key=unit_key,
        canonical_payload_bytes=canonical_payload_bytes,
    )
    judge_count = sum(
        1
        for item in validated
        if item.event.event == "JUDGE" and item.payload["attempt_id"] == attempt_id
    )
    if speak_state != "exact" or judge_count != 0:
        raise AssessmentProducerHistoryError(
            "post-SPEAK confirmation did not observe the exact legal partial state"
        )


def _append_judge(
    event_log: EventLog,
    unit_key: str,
    payload: dict[str, object],
) -> Event:
    try:
        stored = event_log.log("JUDGE", unit_key, payload)
    except Exception as exc:
        raise AssessmentProducerAppendError("JUDGE append failed") from exc
    if type(stored) is not Event:
        raise AssessmentProducerAppendError("JUDGE append returned a non-Event")
    return stored


def _append_speak(
    event_log: EventLog,
    unit_key: str,
    payload: dict[str, object],
) -> Event:
    try:
        stored = event_log.log("SPEAK", unit_key, payload)
    except Exception as exc:
        raise AssessmentProducerAppendError("SPEAK append failed") from exc
    if type(stored) is not Event:
        raise AssessmentProducerAppendError("SPEAK append returned a non-Event")
    return stored
