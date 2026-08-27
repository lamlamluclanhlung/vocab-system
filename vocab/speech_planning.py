"""Pure D66 atomic SPEAK/JUDGE planning from sealed speech evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .artifact_json import canonical_json_bytes, strict_json_loads
from .assessment_evidence import (
    ValidatedAttemptEvidence,
    ValidatedUnitEvidence,
    _require_attempt_evidence,
    _require_attempt_unit_binding,
    _require_unit_evidence,
)
from .assessment_planning import (
    POLICY_ID,
    POLICY_VERSION,
    _validated_policy_stage,
    _validated_presence_stage,
    _validated_review_stage,
    _validated_semantic_stage,
)
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_ATTEMPT_ID_PATTERN,
    ASSESSMENT_FAILURE_CODES_BY_CHANNEL,
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
    ASSESSMENT_STIMULUS_REF_PATTERN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)
from .presence_evidence import (
    PRESENCE_GATE_ID,
    PRESENCE_GATE_VERSION,
    PresenceGateEvidence,
    _require_presence_evidence,
)
from .semantic_evidence import (
    T11SemanticEvidenceBundle,
    _require_semantic_evidence,
)
from .semantic_response import SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES
from .transcription_evidence import (
    TranscriptionEvidence,
    _require_transcription_evidence,
)
from .transcription_ledger import (
    TranscriptionLedgerError,
    _validated_transcription_union,
)


class SpeechPlanningError(ValueError):
    """Raised when speech evidence cannot form one exact atomic plan."""


_PLANNED_SPEECH_SEAL = object()
_PLANNED_SPEECH_SNAPSHOT_DOMAIN = "vocab.t12.planned-speech-assessment"
_PLANNED_SPEECH_SNAPSHOT_VERSION = 1
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_ATTEMPT_ID_RE = re.compile(ASSESSMENT_ATTEMPT_ID_PATTERN)
_STIMULUS_REF_RE = re.compile(ASSESSMENT_STIMULUS_REF_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_COMMON_SPEAK_FIELDS = frozenset(
    (
        "audio_path",
        "transcript",
        "passed",
        "model_id",
        "model_version",
        "channel",
        "producer",
        "producer_version",
        "attempt_id",
        "presented_stimulus_ref",
        "response_audio_ref",
        "outcome",
        "authority_kind",
        "provenance",
    )
)
_COMMON_JUDGE_FIELDS = frozenset(
    (
        "channel",
        "passed",
        "model_id",
        "model_version",
        "producer",
        "producer_version",
        "attempt_id",
        "presented_stimulus_ref",
        "response_artifact_ref",
        "outcome",
        "authority_kind",
        "provenance",
    )
)
_D35_FIELDS = frozenset(("assessment_id", "stimulus_ref", "novel"))


@dataclass(frozen=True, slots=True, init=False)
class PlannedSpeechAssessment:
    """One sealed immutable authority owning companion SPEAK and JUDGE payloads."""

    unit_key: str
    attempt_id: str
    response_audio_ref: str
    _canonical_speak_payload_bytes: bytes = field(repr=False, compare=True)
    _canonical_judge_payload_bytes: bytes = field(repr=False, compare=True)
    _snapshot_bytes: bytes = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __new__(
        cls,
        *_args: object,
        **_kwargs: object,
    ) -> PlannedSpeechAssessment:
        raise TypeError(
            "PlannedSpeechAssessment can only be issued by "
            "plan_speech_assessment"
        )

    @property
    def canonical_speak_payload_bytes(self) -> bytes:
        """Return exact canonical SPEAK bytes after validating the whole pair."""
        _require_planned_speech_assessment(self)
        return self._canonical_speak_payload_bytes

    @property
    def canonical_judge_payload_bytes(self) -> bytes:
        """Return exact canonical JUDGE bytes after validating the whole pair."""
        _require_planned_speech_assessment(self)
        return self._canonical_judge_payload_bytes

    def speak_payload(self) -> dict[str, object]:
        """Return a detached mutable SPEAK payload copy."""
        speak, _ = _require_planned_speech_assessment(self)
        return speak

    def judge_payload(self) -> dict[str, object]:
        """Return a detached mutable JUDGE payload copy."""
        _, judge = _require_planned_speech_assessment(self)
        return judge


def plan_speech_assessment(
    *,
    attempt: ValidatedAttemptEvidence,
    unit: ValidatedUnitEvidence,
    transcription: TranscriptionEvidence,
    presence: PresenceGateEvidence | None = None,
    semantic: T11SemanticEvidenceBundle | None = None,
) -> PlannedSpeechAssessment:
    """Plan one captured S attempt without I/O, clocks, logs, or mutation."""
    validated_attempt = _require_attempt_evidence(attempt)
    validated_unit = _require_unit_evidence(unit)
    _require_attempt_unit_binding(validated_attempt, validated_unit)
    if validated_attempt.channel != "S":
        raise SpeechPlanningError("speech planning requires an S attempt")
    validated_transcription = _require_transcription_evidence(
        transcription,
        attempt=validated_attempt,
    )
    provenance: dict[str, object] = {
        "transcription": validated_transcription.to_provenance()
    }

    status = validated_transcription.status
    transcript = ""
    if status == "UNCERTAIN":
        if presence is not None or semantic is not None:
            raise SpeechPlanningError(
                "UNCERTAIN transcription forbids presence and semantic evidence"
            )
        outcome = ASSESSMENT_OUTCOME_ABSTAIN
        code_name = "reason_code"
        code = "transcription_uncertain"
        authority_kind = "policy"
        model_id = POLICY_ID
        model_version = str(POLICY_VERSION)
        provenance["policy"] = _policy_provenance()
    elif status == "FAILED":
        if presence is not None or semantic is not None:
            raise SpeechPlanningError(
                "FAILED transcription forbids presence and semantic evidence"
            )
        outcome = ASSESSMENT_OUTCOME_ABSTAIN
        code_name = "reason_code"
        code = validated_transcription.failure_code
        authority_kind = "policy"
        model_id = POLICY_ID
        model_version = str(POLICY_VERSION)
        provenance["policy"] = _policy_provenance()
    elif status == "SUCCESS":
        transcript = validated_transcription.approved_transcript_text
        if presence is None:
            raise SpeechPlanningError(
                "SUCCESS transcription requires speech presence evidence"
            )
        validated_presence = _require_presence_evidence(
            presence,
            attempt=validated_attempt,
            unit=validated_unit,
            transcription=validated_transcription,
        )
        provenance["presence_gate"] = validated_presence.to_provenance()
        if not validated_presence.target_present:
            if semantic is not None:
                raise SpeechPlanningError(
                    "target-absent speech planning forbids semantic evidence"
                )
            outcome = ASSESSMENT_OUTCOME_OMITTED
            code_name = "reason_code"
            code = "target_absent"
            authority_kind = "deterministic_gate"
            model_id = PRESENCE_GATE_ID
            model_version = str(PRESENCE_GATE_VERSION)
        else:
            if semantic is None:
                raise SpeechPlanningError(
                    "target-present speech planning requires semantic evidence"
                )
            _, material = _require_semantic_evidence(
                semantic,
                attempt=validated_attempt,
                unit=validated_unit,
                presence=validated_presence,
                transcription=validated_transcription,
            )
            result = material.result
            outcome = result.outcome
            provenance["semantic_judge"] = dict(
                material.proposal.semantic_judge_facts
            )
            provenance["human_review"] = {
                "reviewer_id": material.review.reviewer_id,
                "reviewer_version": material.review.reviewer_version,
                "decision": material.review.decision,
            }
            if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
                authority_kind = "semantic_model"
                model_id = material.proposal.assessor_id
                model_version = material.proposal.assessor_version
                code_name = (
                    "failure_code"
                    if outcome == ASSESSMENT_OUTCOME_FAIL
                    else ""
                )
                code = result.failure_code
            elif outcome == ASSESSMENT_OUTCOME_ABSTAIN:
                authority_kind = "policy"
                model_id = POLICY_ID
                model_version = str(POLICY_VERSION)
                code_name = "reason_code"
                code = result.reason_code
                provenance["policy"] = _policy_provenance()
            else:  # pragma: no cover - T11 materialization closes this first
                raise SpeechPlanningError(
                    "semantic speech outcome is unsupported"
                )
    else:  # pragma: no cover - sealed evidence closes this first
        raise SpeechPlanningError("transcription status is unsupported")

    speak = _base_speak_payload(
        attempt=validated_attempt,
        transcript=transcript,
        outcome=outcome,
        authority_kind=authority_kind,
        model_id=model_id,
        model_version=model_version,
        provenance=provenance,
    )
    judge = _base_speech_judge_payload(
        attempt=validated_attempt,
        outcome=outcome,
        authority_kind=authority_kind,
        model_id=model_id,
        model_version=model_version,
        provenance=provenance,
    )
    if code_name:
        speak[code_name] = code
        judge[code_name] = code
    if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        judge.update(
            {
                "assessment_id": validated_attempt.attempt_id,
                "stimulus_ref": validated_attempt.presented_stimulus_ref,
                "novel": validated_attempt.novel,
            }
        )
    return _issue_planned_speech_assessment(
        unit_key=validated_unit.unit_key,
        attempt_id=validated_attempt.attempt_id,
        response_audio_ref=validated_attempt.response_artifact_ref,
        speak_payload=speak,
        judge_payload=judge,
    )


def _base_speak_payload(
    *,
    attempt: ValidatedAttemptEvidence,
    transcript: str,
    outcome: str,
    authority_kind: str,
    model_id: str,
    model_version: str,
    provenance: dict[str, object],
) -> dict[str, object]:
    return {
        "audio_path": attempt.response_artifact_ref.removeprefix("sha256:"),
        "transcript": transcript,
        "passed": outcome == ASSESSMENT_OUTCOME_PASS,
        "model_id": model_id,
        "model_version": model_version,
        "channel": "S",
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "attempt_id": attempt.attempt_id,
        "presented_stimulus_ref": attempt.presented_stimulus_ref,
        "response_audio_ref": attempt.response_artifact_ref,
        "outcome": outcome,
        "authority_kind": authority_kind,
        "provenance": provenance,
    }


def _base_speech_judge_payload(
    *,
    attempt: ValidatedAttemptEvidence,
    outcome: str,
    authority_kind: str,
    model_id: str,
    model_version: str,
    provenance: dict[str, object],
) -> dict[str, object]:
    return {
        "channel": "S",
        "passed": outcome == ASSESSMENT_OUTCOME_PASS,
        "model_id": model_id,
        "model_version": model_version,
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "attempt_id": attempt.attempt_id,
        "presented_stimulus_ref": attempt.presented_stimulus_ref,
        "response_artifact_ref": attempt.response_artifact_ref,
        "outcome": outcome,
        "authority_kind": authority_kind,
        "provenance": provenance,
    }


def _issue_planned_speech_assessment(
    *,
    unit_key: str,
    attempt_id: str,
    response_audio_ref: str,
    speak_payload: object,
    judge_payload: object,
) -> PlannedSpeechAssessment:
    speak = _validated_speak_payload(unit_key=unit_key, payload=speak_payload)
    judge = _validated_speech_judge_payload(
        unit_key=unit_key,
        payload=judge_payload,
    )
    _validate_companion_pair(
        unit_key=unit_key,
        response_audio_ref=response_audio_ref,
        speak=speak,
        judge=judge,
    )
    speak_bytes = canonical_json_bytes(speak)
    judge_bytes = canonical_json_bytes(judge)
    planned = object.__new__(PlannedSpeechAssessment)
    object.__setattr__(planned, "unit_key", unit_key)
    object.__setattr__(planned, "attempt_id", attempt_id)
    object.__setattr__(planned, "response_audio_ref", response_audio_ref)
    object.__setattr__(
        planned,
        "_canonical_speak_payload_bytes",
        speak_bytes,
    )
    object.__setattr__(
        planned,
        "_canonical_judge_payload_bytes",
        judge_bytes,
    )
    object.__setattr__(
        planned,
        "_snapshot_bytes",
        _planned_speech_snapshot_bytes(
            unit_key=unit_key,
            attempt_id=attempt_id,
            response_audio_ref=response_audio_ref,
            speak_bytes=speak_bytes,
            judge_bytes=judge_bytes,
        ),
    )
    object.__setattr__(planned, "_seal", _PLANNED_SPEECH_SEAL)
    return planned


def _require_planned_speech_assessment(
    value: object,
) -> tuple[dict[str, object], dict[str, object]]:
    if type(value) is not PlannedSpeechAssessment:
        raise TypeError("planned must be a PlannedSpeechAssessment")
    try:
        seal = value._seal
    except AttributeError:
        raise TypeError(
            "planned speech assessment was not issued by plan_speech_assessment"
        ) from None
    if seal is not _PLANNED_SPEECH_SEAL:
        raise TypeError(
            "planned speech assessment was not issued by plan_speech_assessment"
        )
    if (
        type(value._canonical_speak_payload_bytes) is not bytes
        or type(value._canonical_judge_payload_bytes) is not bytes
    ):
        raise SpeechPlanningError("planned speech canonical payload bytes are invalid")
    try:
        speak_value = strict_json_loads(value._canonical_speak_payload_bytes)
        judge_value = strict_json_loads(value._canonical_judge_payload_bytes)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SpeechPlanningError(
            "planned speech canonical payload bytes are invalid"
        ) from exc
    speak = _validated_speak_payload(
        unit_key=value.unit_key,
        payload=speak_value,
    )
    judge = _validated_speech_judge_payload(
        unit_key=value.unit_key,
        payload=judge_value,
    )
    if canonical_json_bytes(speak) != value._canonical_speak_payload_bytes:
        raise SpeechPlanningError("planned SPEAK bytes are not exact canonical JSON")
    if canonical_json_bytes(judge) != value._canonical_judge_payload_bytes:
        raise SpeechPlanningError("planned JUDGE bytes are not exact canonical JSON")
    _validate_companion_pair(
        unit_key=value.unit_key,
        response_audio_ref=value.response_audio_ref,
        speak=speak,
        judge=judge,
    )
    if (
        speak["attempt_id"] != value.attempt_id
        or judge["attempt_id"] != value.attempt_id
    ):
        raise SpeechPlanningError(
            "planned pair attempt identity disagrees with its owner"
        )
    if type(value._snapshot_bytes) is not bytes:
        raise SpeechPlanningError("planned speech issuance snapshot is invalid")
    current_snapshot = _planned_speech_snapshot_bytes(
        unit_key=value.unit_key,
        attempt_id=value.attempt_id,
        response_audio_ref=value.response_audio_ref,
        speak_bytes=value._canonical_speak_payload_bytes,
        judge_bytes=value._canonical_judge_payload_bytes,
    )
    if current_snapshot != value._snapshot_bytes:
        raise SpeechPlanningError(
            "planned speech runtime fields disagree with its issuance snapshot"
        )
    return speak, judge


def _validated_speak_payload(
    *,
    unit_key: object,
    payload: object,
) -> dict[str, object]:
    _require_unit_key(unit_key)
    source = _require_payload_mapping(payload, "SPEAK")
    outcome = source.get("outcome")
    if outcome == ASSESSMENT_OUTCOME_PASS:
        expected = _COMMON_SPEAK_FIELDS
    elif outcome == ASSESSMENT_OUTCOME_FAIL:
        expected = _COMMON_SPEAK_FIELDS | {"failure_code"}
    elif outcome in (ASSESSMENT_OUTCOME_OMITTED, ASSESSMENT_OUTCOME_ABSTAIN):
        expected = _COMMON_SPEAK_FIELDS | {"reason_code"}
    else:
        raise SpeechPlanningError("planned SPEAK outcome is unsupported")
    if set(source) != expected:
        raise SpeechPlanningError("planned SPEAK payload has the wrong key set")
    _validated_common_speech_fields(source, response_field="response_audio_ref")
    response_ref = source["response_audio_ref"]
    if source["audio_path"] != response_ref.removeprefix("sha256:"):
        raise SpeechPlanningError(
            "planned SPEAK audio_path is not the exact response digest suffix"
        )
    if (
        type(source["audio_path"]) is not str
        or _LOWER_SHA256_RE.fullmatch(source["audio_path"]) is None
    ):
        raise SpeechPlanningError("planned SPEAK audio_path is invalid")
    if type(source["transcript"]) is not str:
        raise SpeechPlanningError("planned SPEAK transcript must be a string")
    _validated_speech_outcome_closure(source, speak=True)
    return _detached_payload(source, "SPEAK")


def _validated_speech_judge_payload(
    *,
    unit_key: object,
    payload: object,
) -> dict[str, object]:
    _require_unit_key(unit_key)
    source = _require_payload_mapping(payload, "JUDGE")
    outcome = source.get("outcome")
    if outcome == ASSESSMENT_OUTCOME_PASS:
        expected = _COMMON_JUDGE_FIELDS | _D35_FIELDS
    elif outcome == ASSESSMENT_OUTCOME_FAIL:
        expected = _COMMON_JUDGE_FIELDS | _D35_FIELDS | {"failure_code"}
    elif outcome in (ASSESSMENT_OUTCOME_OMITTED, ASSESSMENT_OUTCOME_ABSTAIN):
        expected = _COMMON_JUDGE_FIELDS | {"reason_code"}
    else:
        raise SpeechPlanningError("planned speech JUDGE outcome is unsupported")
    if set(source) != expected:
        raise SpeechPlanningError(
            "planned speech JUDGE payload has the wrong key set"
        )
    _validated_common_speech_fields(
        source,
        response_field="response_artifact_ref",
    )
    _validated_speech_outcome_closure(source, speak=False)
    if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        if source["assessment_id"] != source["attempt_id"]:
            raise SpeechPlanningError("speech assessment_id must equal attempt_id")
        if source["stimulus_ref"] != source["presented_stimulus_ref"]:
            raise SpeechPlanningError(
                "speech stimulus_ref must equal presented_stimulus_ref"
            )
        if type(source["novel"]) is not bool:
            raise SpeechPlanningError("speech novel must be an actual Boolean")
    return _detached_payload(source, "JUDGE")


def _validated_common_speech_fields(
    payload: Mapping[object, object],
    *,
    response_field: str,
) -> None:
    if payload["channel"] != "S":
        raise SpeechPlanningError("planned speech channel must be S")
    outcome = payload["outcome"]
    if type(payload["passed"]) is not bool or payload["passed"] is not (
        outcome == ASSESSMENT_OUTCOME_PASS
    ):
        raise SpeechPlanningError("planned speech passed/outcome invariant failed")
    if payload["producer"] != T12_ASSESSMENT_PRODUCER_ID:
        raise SpeechPlanningError("planned speech producer is invalid")
    if (
        type(payload["producer_version"]) is not int
        or payload["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise SpeechPlanningError("planned speech producer_version is invalid")
    _require_pattern(payload["attempt_id"], _ATTEMPT_ID_RE, "attempt_id")
    _require_pattern(
        payload["presented_stimulus_ref"],
        _STIMULUS_REF_RE,
        "presented_stimulus_ref",
    )
    _require_pattern(payload[response_field], _ARTIFACT_REF_RE, response_field)
    for name in ("model_id", "model_version"):
        if type(payload[name]) is not str or not payload[name]:
            raise SpeechPlanningError(f"planned speech {name} is invalid")


def _validated_speech_outcome_closure(
    payload: Mapping[object, object],
    *,
    speak: bool,
) -> None:
    provenance = payload["provenance"]
    if not isinstance(provenance, Mapping):
        raise SpeechPlanningError("planned speech provenance must be an object")
    if "transcription" not in provenance:
        raise SpeechPlanningError("planned speech provenance lacks transcription")
    try:
        transcription = _validated_transcription_union(
            provenance["transcription"],
            location="planned speech transcription provenance",
        )
    except TranscriptionLedgerError as exc:
        raise SpeechPlanningError(str(exc)) from None

    outcome = payload["outcome"]
    status = transcription["status"]
    expected_stages: set[str]
    if status == "UNCERTAIN":
        if (
            outcome != ASSESSMENT_OUTCOME_ABSTAIN
            or payload.get("reason_code") != "transcription_uncertain"
        ):
            raise SpeechPlanningError("UNCERTAIN transcription closure is invalid")
        expected_stages = {"transcription", "policy"}
        _require_top_level_authority(
            payload,
            kind="policy",
            model_id=POLICY_ID,
            model_version=str(POLICY_VERSION),
        )
    elif status == "FAILED":
        if (
            outcome != ASSESSMENT_OUTCOME_ABSTAIN
            or payload.get("reason_code") != transcription["failure_code"]
        ):
            raise SpeechPlanningError("FAILED transcription closure is invalid")
        expected_stages = {"transcription", "policy"}
        _require_top_level_authority(
            payload,
            kind="policy",
            model_id=POLICY_ID,
            model_version=str(POLICY_VERSION),
        )
    else:
        if "presence_gate" not in provenance:
            raise SpeechPlanningError(
                "SUCCESS transcription requires presence provenance"
            )
        try:
            target_present = _validated_presence_stage(
                provenance["presence_gate"]
            )
        except ValueError as exc:
            raise SpeechPlanningError(str(exc)) from None
        if not target_present:
            if (
                outcome != ASSESSMENT_OUTCOME_OMITTED
                or payload.get("reason_code") != "target_absent"
            ):
                raise SpeechPlanningError("target-absent speech closure is invalid")
            expected_stages = {"transcription", "presence_gate"}
            _require_top_level_authority(
                payload,
                kind="deterministic_gate",
                model_id=PRESENCE_GATE_ID,
                model_version=str(PRESENCE_GATE_VERSION),
            )
        else:
            expected_stages = {
                "transcription",
                "presence_gate",
                "semantic_judge",
                "human_review",
            }
            try:
                semantic_stage = _validated_semantic_stage(
                    provenance.get("semantic_judge")
                )
                review_stage = _validated_review_stage(
                    provenance.get("human_review")
                )
            except ValueError as exc:
                raise SpeechPlanningError(str(exc)) from None
            if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
                _require_top_level_authority(
                    payload,
                    kind="semantic_model",
                    model_id=semantic_stage["assessor_id"],
                    model_version=semantic_stage["assessor_version"],
                )
                if review_stage["decision"] != "APPROVE":
                    raise SpeechPlanningError(
                        "semantic PASS/FAIL requires APPROVE review"
                    )
                if outcome == ASSESSMENT_OUTCOME_FAIL:
                    if payload.get("failure_code") not in (
                        ASSESSMENT_FAILURE_CODES_BY_CHANNEL["S"]
                    ):
                        raise SpeechPlanningError(
                            "speech failure_code is invalid"
                        )
            elif outcome == ASSESSMENT_OUTCOME_ABSTAIN:
                reason_code = payload.get("reason_code")
                if reason_code not in (
                    *SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES,
                    "reviewer_rejected",
                ):
                    raise SpeechPlanningError(
                        "semantic speech ABSTAIN reason is invalid"
                    )
                expected_stages.add("policy")
                _require_top_level_authority(
                    payload,
                    kind="policy",
                    model_id=POLICY_ID,
                    model_version=str(POLICY_VERSION),
                )
                expected_decision = (
                    "REJECT" if reason_code == "reviewer_rejected" else "APPROVE"
                )
                if review_stage["decision"] != expected_decision:
                    raise SpeechPlanningError(
                        "human-review decision disagrees with speech ABSTAIN"
                    )
            else:
                raise SpeechPlanningError(
                    "target-present SUCCESS speech outcome is invalid"
                )

    if set(provenance) != expected_stages:
        raise SpeechPlanningError("planned speech provenance has wrong stages")
    if "policy" in provenance:
        try:
            _validated_policy_stage(provenance["policy"])
        except ValueError as exc:
            raise SpeechPlanningError(str(exc)) from None
    if speak:
        transcript = payload["transcript"]
        if status == "SUCCESS":
            try:
                transcript_bytes = transcript.encode("utf-8")
            except UnicodeEncodeError:
                raise SpeechPlanningError(
                    "SPEAK transcript is not strict UTF-8 encodable"
                ) from None
            expected_ref = "sha256:" + hashlib.sha256(transcript_bytes).hexdigest()
            if (
                not transcript.strip()
                or expected_ref != transcription["approved_transcript_ref"]
            ):
                raise SpeechPlanningError(
                    "SPEAK transcript does not exactly match approved transcript ref"
                )
        elif transcript != "":
            raise SpeechPlanningError(
                "non-SUCCESS SPEAK transcript must be empty"
            )


def _validate_companion_pair(
    *,
    unit_key: object,
    response_audio_ref: object,
    speak: Mapping[object, object],
    judge: Mapping[object, object],
) -> None:
    _require_unit_key(unit_key)
    validated_response_ref = _require_pattern(
        response_audio_ref,
        _ARTIFACT_REF_RE,
        "pair response_audio_ref",
    )
    field_pairs = (
        ("producer", "producer"),
        ("producer_version", "producer_version"),
        ("attempt_id", "attempt_id"),
        ("channel", "channel"),
        ("presented_stimulus_ref", "presented_stimulus_ref"),
        ("outcome", "outcome"),
        ("passed", "passed"),
        ("model_id", "model_id"),
        ("model_version", "model_version"),
        ("authority_kind", "authority_kind"),
        ("provenance", "provenance"),
    )
    for speak_name, judge_name in field_pairs:
        if speak[speak_name] != judge[judge_name]:
            raise SpeechPlanningError(
                f"SPEAK/JUDGE companion mismatch for {speak_name}"
            )
    if (
        speak["response_audio_ref"] != judge["response_artifact_ref"]
        or speak["response_audio_ref"] != validated_response_ref
    ):
        raise SpeechPlanningError("SPEAK/JUDGE raw-audio identity mismatch")
    for code_name in ("failure_code", "reason_code"):
        if speak.get(code_name) != judge.get(code_name):
            raise SpeechPlanningError(
                f"SPEAK/JUDGE companion mismatch for {code_name}"
            )


def _planned_speech_snapshot_bytes(
    *,
    unit_key: str,
    attempt_id: str,
    response_audio_ref: str,
    speak_bytes: bytes,
    judge_bytes: bytes,
) -> bytes:
    return canonical_json_bytes(
        {
            "domain": _PLANNED_SPEECH_SNAPSHOT_DOMAIN,
            "v": _PLANNED_SPEECH_SNAPSHOT_VERSION,
            "unit_key": unit_key,
            "attempt_id": attempt_id,
            "response_audio_ref": response_audio_ref,
            "speak_sha256": hashlib.sha256(speak_bytes).hexdigest(),
            "judge_sha256": hashlib.sha256(judge_bytes).hexdigest(),
        }
    )


def _policy_provenance() -> dict[str, object]:
    return {"policy_id": POLICY_ID, "policy_version": POLICY_VERSION}


def _require_top_level_authority(
    payload: Mapping[object, object],
    *,
    kind: str,
    model_id: object,
    model_version: object,
) -> None:
    if (
        payload["authority_kind"] != kind
        or payload["model_id"] != model_id
        or payload["model_version"] != model_version
    ):
        raise SpeechPlanningError(
            "planned speech top-level authority is incoherent"
        )


def _require_unit_key(value: object) -> str:
    if type(value) is not str or _UNIT_KEY_RE.fullmatch(value) is None:
        raise SpeechPlanningError("planned speech unit_key is invalid")
    return value


def _require_pattern(
    value: object,
    pattern: re.Pattern[str],
    name: str,
) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise SpeechPlanningError(f"planned speech {name} is invalid")
    return value


def _require_payload_mapping(
    payload: object,
    name: str,
) -> Mapping[object, object]:
    if not isinstance(payload, Mapping):
        raise SpeechPlanningError(f"planned {name} payload must be an object")
    return payload


def _detached_payload(
    payload: Mapping[object, object],
    name: str,
) -> dict[str, object]:
    try:
        detached = strict_json_loads(canonical_json_bytes(payload))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SpeechPlanningError(
            f"planned {name} payload is not canonical-JSON serializable"
        ) from exc
    if type(detached) is not dict:  # pragma: no cover - mapping input guarantees it
        raise AssertionError(f"planned {name} payload is not an object")
    return detached
