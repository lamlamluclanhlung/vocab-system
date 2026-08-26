"""Adversarial D65/D66 tests for speech evidence and atomic speech planning."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import vocab.speech_planning as speech_module
from vocab.artifact_json import canonical_json_bytes
from vocab.artifact_store import ArtifactStore
from vocab.assessment_evidence import (
    ValidatedAttemptEvidence,
    load_validated_attempt_evidence,
    validate_unit_evidence,
)
from vocab.assessment_identity import assessment_attempt_id, cognitive_stimulus_ref
from vocab.exposure import reserve_exposure
from vocab.human_review import build_human_review, serialize_human_review
from vocab.models import VocabUnit
from vocab.presence_evidence import (
    PresenceEvidenceError,
    evaluate_speech_presence_gate,
)
from vocab.response_capture import capture_response, initialize_t12_ledgers
from vocab.semantic_evidence import (
    SemanticEvidenceError,
    bind_t11_semantic_evidence,
)
from vocab.semantic_request import (
    build_semantic_request,
    semantic_request_digest,
    serialize_semantic_request,
)
from vocab.semantic_response import import_semantic_response
from vocab.session import (
    SessionManifest,
    create_session_manifest,
    persist_session_manifest,
)
from vocab.speech_planning import (
    PlannedSpeechAssessment,
    SpeechPlanningError,
    plan_speech_assessment,
)
from vocab.transcription_evidence import (
    TranscriptionEvidenceError,
    load_transcription_evidence,
)
from vocab.transcription_ledger import (
    TranscriptionLedgerError,
    append_transcription_record,
    build_transcription_receipt,
    read_transcription_ledger,
)


UNIT_KEY = "subtle::small-difference"
DEFINITION = "not immediately obvious or easy to notice"
ASSESSOR_ID = "GPT-5.6"
ASSESSOR_VERSION = "version-unavailable-from-ui"
CREATED_AT = "2026-08-25T02:00:00+00:00"
RESERVED_AT = "2026-08-25T02:01:00+00:00"
CAPTURED_AT = "2026-08-25T02:02:00+00:00"
RECORDED_AT = "2026-08-25T02:03:00+00:00"

S_STIMULUS = {
    "production_prompt": "Compare two nearly identical research results aloud.",
    "semantic_constraints": "Use subtle for a small hard-to-notice difference.",
}
R_STIMULUS = {
    "passage": "The distinction between the proposals was subtle.",
    "question": "How was the distinction described?",
}


@dataclass
class SpeechRuntime:
    store: ArtifactStore
    exposure_path: Path
    capture_path: Path
    disposition_path: Path
    transcription_path: Path
    session_root: Path
    manifest: SessionManifest
    item: dict[str, object]
    attempt_id: str
    raw_audio: bytes

    def attempt(self) -> ValidatedAttemptEvidence:
        return load_validated_attempt_evidence(
            exposure_path=self.exposure_path,
            capture_path=self.capture_path,
            disposition_path=self.disposition_path,
            artifact_store=self.store,
            session_root=self.session_root,
            attempt_id=self.attempt_id,
        )


def make_unit(channel: str = "S") -> VocabUnit:
    values: dict[str, object] = {
        "unit_key": UNIT_KEY,
        "lemma": "subtle",
        "lemma_slug": "subtle",
        "sense_slug": "small-difference",
        "unit_type": "word",
        "register": "neutral",
        "definition_en": DEFINITION,
        "source_ref": "dictionary:cambridge:subtle",
        "source_sentence": "The distinction was subtle but important.",
    }
    values[f"Target_{channel}"] = "1"
    values[f"state_{channel}"] = "NEW"
    return VocabUnit(**values)  # type: ignore[arg-type]


def make_item(store: ArtifactStore, channel: str = "S") -> dict[str, object]:
    if channel == "S":
        task_kind = "spoken_production"
        stimulus = dict(S_STIMULUS)
    elif channel == "R":
        task_kind = "reading_comprehension"
        stimulus = dict(R_STIMULUS)
    else:  # pragma: no cover - helpers intentionally support only required cases
        raise AssertionError("unsupported helper channel")
    stimulus_ref = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel=channel,
        task_kind=task_kind,
        stimulus=stimulus,
    )
    return {
        "item_ordinal": 0,
        "unit_key": UNIT_KEY,
        "channel": channel,
        "task_kind": task_kind,
        "stimulus": stimulus,
        "presented_stimulus_ref": stimulus_ref,
        "stimulus_artifact_ref": store.put(canonical_json_bytes(stimulus)),
    }


def make_runtime(
    tmp_path: Path,
    *,
    channel: str = "S",
    raw_audio: bytes = b"\x00raw learner audio\xff",
    reserve: bool = True,
    capture: bool = True,
) -> SpeechRuntime:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    transcription_path = tmp_path / "t12-transcriptions.jsonl"
    session_root = tmp_path / "sessions"
    initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=True,
    )
    transcription_path.write_bytes(b"")
    item = make_item(store, channel)
    manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    persist_session_manifest(session_root, manifest)
    attempt_id = assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel=channel,
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    if reserve:
        permit = reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=store,
            session_root=session_root,
            session_id=manifest.session_id,
            item_ordinal=0,
            reserved_at=RESERVED_AT,
        )
        if capture:
            permit.consume()
            capture_response(
                exposure_path=exposure_path,
                capture_path=capture_path,
                disposition_path=disposition_path,
                artifact_store=store,
                captured_at=CAPTURED_AT,
                display_permit=permit,
                response_bytes=raw_audio,
            )
    return SpeechRuntime(
        store=store,
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        transcription_path=transcription_path,
        session_root=session_root,
        manifest=manifest,
        item=item,
        attempt_id=attempt_id,
        raw_audio=raw_audio,
    )


def append_attempt(runtime: SpeechRuntime) -> SpeechRuntime:
    manifest = create_session_manifest(created_at=CREATED_AT, items=[runtime.item])
    persist_session_manifest(runtime.session_root, manifest)
    attempt_id = assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel=runtime.item["channel"],
        presented_stimulus_ref=runtime.item["presented_stimulus_ref"],
    )
    permit = reserve_exposure(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        session_root=runtime.session_root,
        session_id=manifest.session_id,
        item_ordinal=0,
        reserved_at=RESERVED_AT,
    )
    permit.consume()
    capture_response(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=runtime.raw_audio,
    )
    return SpeechRuntime(
        store=runtime.store,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        transcription_path=runtime.transcription_path,
        session_root=runtime.session_root,
        manifest=manifest,
        item=runtime.item,
        attempt_id=attempt_id,
        raw_audio=runtime.raw_audio,
    )


def make_stage(
    runtime: SpeechRuntime,
    status: str = "SUCCESS",
    *,
    candidate: str = "There is a subtle difference between the results.",
    approved: str = "There is a subtle difference between the results.",
    uncertainty_code: str = "transcript_ambiguous",
    failure_code: str = "transcription_failed",
    after_stt: bool = False,
) -> dict[str, object]:
    metadata = {
        "stt_model_id": "local-stt",
        "stt_model_version": "1",
        "decoder_version": "greedy-v1",
    }
    if status == "SUCCESS":
        return {
            "status": "SUCCESS",
            **metadata,
            "stt_output_ref": runtime.store.put(candidate.encode("utf-8")),
            "approved_transcript_ref": runtime.store.put(
                approved.encode("utf-8")
            ),
            "verifier_id": "verifier-a",
            "verifier_version": 1,
        }
    if status == "UNCERTAIN":
        return {
            "status": "UNCERTAIN",
            **metadata,
            "stt_output_ref": runtime.store.put(candidate.encode("utf-8")),
            "verifier_id": "verifier-a",
            "verifier_version": 1,
            "uncertainty_code": uncertainty_code,
        }
    stage: dict[str, object] = {
        "status": "FAILED",
        "failure_code": failure_code,
    }
    if after_stt:
        stage = {"status": "FAILED", **metadata, "failure_code": failure_code}
    return stage


def build_receipt(
    runtime: SpeechRuntime,
    stage: dict[str, object],
    *,
    attempt_id: str | None = None,
    response_audio_ref: str | None = None,
    recorded_at: str = RECORDED_AT,
):
    return build_transcription_receipt(
        recorded_at=recorded_at,
        attempt_id=runtime.attempt_id if attempt_id is None else attempt_id,
        response_audio_ref=(
            "sha256:" + hashlib.sha256(runtime.raw_audio).hexdigest()
            if response_audio_ref is None
            else response_audio_ref
        ),
        transcription=stage,
    )


def append_receipt(runtime: SpeechRuntime, receipt) -> None:
    append_transcription_record(
        runtime.transcription_path,
        receipt,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        artifact_store=runtime.store,
    )


def record_disposition(
    runtime: SpeechRuntime,
    status: str = "SUCCESS",
    **stage_kwargs: object,
):
    stage = make_stage(runtime, status, **stage_kwargs)  # type: ignore[arg-type]
    receipt = build_receipt(runtime, stage)
    append_receipt(runtime, receipt)
    return receipt


def load_transcription(
    runtime: SpeechRuntime,
    attempt: ValidatedAttemptEvidence | None = None,
):
    return load_transcription_evidence(
        transcription_path=runtime.transcription_path,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        artifact_store=runtime.store,
        attempt=runtime.attempt() if attempt is None else attempt,
    )


def make_semantic_artifacts(
    *,
    transcript: str,
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
    request_transcript: str | None = None,
) -> tuple[bytes, bytes, bytes]:
    request = build_semantic_request(
        unit_key=UNIT_KEY,
        lemma="subtle",
        unit_type="word",
        definition_en=DEFINITION,
        channel="S",
        task_content={
            **S_STIMULUS,
            "approved_transcript": (
                transcript if request_transcript is None else request_transcript
            ),
        },
    )
    proposal = {
        "artifact": "vocab.t11.semantic-response",
        "v": 1,
        "request_digest": semantic_request_digest(request),
        "outcome": outcome,
        "failure_code": failure_code,
        "reason_code": reason_code,
        "semantic_rationale": "The target-specific evidence supports this result.",
    }
    proposal_raw = canonical_json_bytes(proposal)
    imported = import_semantic_response(
        proposal_raw,
        request=request,
        assessor_id=ASSESSOR_ID,
        assessor_version=ASSESSOR_VERSION,
    )
    review = build_human_review(
        imported_proposal=imported,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision=decision,
    )
    review_raw = serialize_human_review(review, imported_proposal=imported)
    return serialize_semantic_request(request), proposal_raw, review_raw


def bind_semantic(
    *,
    attempt: ValidatedAttemptEvidence,
    unit,
    transcription,
    presence,
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
    request_transcript: str | None = None,
):
    artifacts = make_semantic_artifacts(
        transcript=transcription.approved_transcript_text,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
        decision=decision,
        request_transcript=request_transcript,
    )
    bundle = bind_t11_semantic_evidence(
        request_raw=artifacts[0],
        proposal_raw=artifacts[1],
        review_raw=artifacts[2],
        assessor_id=ASSESSOR_ID,
        assessor_version=ASSESSOR_VERSION,
        attempt=attempt,
        unit=unit,
        presence=presence,
        transcription=transcription,
    )
    return bundle, artifacts


def planned_success(
    tmp_path: Path,
    *,
    approved: str = "There is a subtle difference between the results.",
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
) -> tuple[PlannedSpeechAssessment, SpeechRuntime, object, object, object, object]:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, approved=approved)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    transcription = load_transcription(runtime, attempt)
    presence = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=transcription,
    )
    semantic, _ = bind_semantic(
        attempt=attempt,
        unit=unit,
        transcription=transcription,
        presence=presence,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
        decision=decision,
    )
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=unit,
        transcription=transcription,
        presence=presence,
        semantic=semantic,
    )
    return plan, runtime, attempt, transcription, presence, semantic


# ---------------------------------------------------------------------------
# D65 ledger, interruption, artifact identity, and exact union closure
# ---------------------------------------------------------------------------


def test_missing_exposure_prevents_transcription_append(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, reserve=False, capture=False)
    receipt = build_receipt(runtime, make_stage(runtime))
    with pytest.raises(TranscriptionLedgerError, match="exposure|capture history"):
        append_receipt(runtime, receipt)


def test_missing_capture_prevents_transcription_append(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, capture=False)
    receipt = build_receipt(runtime, make_stage(runtime))
    with pytest.raises(TranscriptionLedgerError, match="capture"):
        append_receipt(runtime, receipt)


def test_capture_for_non_s_attempt_prevents_transcription_append(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, channel="R", raw_audio=b"text capture")
    receipt = build_receipt(runtime, make_stage(runtime))
    with pytest.raises(TranscriptionLedgerError, match="S attempt"):
        append_receipt(runtime, receipt)


def test_response_audio_ref_must_equal_capture_ref(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    other_ref = runtime.store.put(b"different raw audio")
    receipt = build_receipt(
        runtime,
        make_stage(runtime),
        response_audio_ref=other_ref,
    )
    with pytest.raises(TranscriptionLedgerError, match="capture receipt"):
        append_receipt(runtime, receipt)


@pytest.mark.parametrize("corruption", ("missing", "changed"))
def test_raw_audio_artifact_must_verify(
    tmp_path: Path,
    corruption: str,
) -> None:
    runtime = make_runtime(tmp_path)
    raw_ref = "sha256:" + hashlib.sha256(runtime.raw_audio).hexdigest()
    path = runtime.store.root / raw_ref.removeprefix("sha256:")
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    receipt = build_receipt(runtime, make_stage(runtime))
    with pytest.raises(TranscriptionLedgerError, match="capture history"):
        append_receipt(runtime, receipt)


def test_duplicate_identical_transcription_slot_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    receipt = build_receipt(runtime, make_stage(runtime))
    append_receipt(runtime, receipt)
    with pytest.raises(TranscriptionLedgerError, match="slot"):
        append_receipt(runtime, receipt)


def test_duplicate_different_transcription_slot_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    append_receipt(runtime, build_receipt(runtime, make_stage(runtime)))
    changed = build_receipt(
        runtime,
        make_stage(runtime, approved="A subtle difference remained."),
        recorded_at="2026-08-25T02:04:00+00:00",
    )
    with pytest.raises(TranscriptionLedgerError, match="slot"):
        append_receipt(runtime, changed)


def test_malformed_final_transcription_record_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transcription_path.write_bytes(b'{"v":1')
    with pytest.raises(TranscriptionLedgerError, match="malformed final"):
        read_transcription_ledger(runtime.transcription_path)


def test_unknown_receipt_field_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    value = build_receipt(runtime, make_stage(runtime)).to_dict()
    value["unknown"] = True
    runtime.transcription_path.write_bytes(canonical_json_bytes(value) + b"\n")
    with pytest.raises(TranscriptionLedgerError, match="key set"):
        read_transcription_ledger(runtime.transcription_path)


def test_noncanonical_transcription_jsonl_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    value = build_receipt(runtime, make_stage(runtime)).to_dict()
    runtime.transcription_path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    )
    with pytest.raises(TranscriptionLedgerError, match="not canonical"):
        read_transcription_ledger(runtime.transcription_path)


def test_invalid_utf8_transcription_jsonl_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transcription_path.write_bytes(b"\xff\n")
    with pytest.raises(TranscriptionLedgerError, match="invalid"):
        read_transcription_ledger(runtime.transcription_path)


def test_unknown_transcription_union_field_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    stage = make_stage(runtime)
    stage["unknown"] = True
    with pytest.raises(TranscriptionLedgerError, match="key set"):
        build_receipt(runtime, stage)


def test_interruption_without_terminal_receipt_has_no_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.store.put(b"orphan STT candidate")
    with pytest.raises(TranscriptionEvidenceError, match="exactly one"):
        load_transcription(runtime)
    assert read_transcription_ledger(runtime.transcription_path) == ()


def test_verification_not_performed_is_not_a_failure_code(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(TranscriptionLedgerError, match="failure_code"):
        make_stage(runtime, failure_code="verification_not_performed")
        build_receipt(
            runtime,
            {"status": "FAILED", "failure_code": "verification_not_performed"},
        )


def test_interruption_is_not_auto_materialized_as_infrastructure_failure(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    _ = runtime.store.put(b"candidate created before process crash")
    assert runtime.transcription_path.read_bytes() == b""
    assert read_transcription_ledger(runtime.transcription_path) == ()


def test_same_candidate_bytes_have_same_artifact_ref(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    assert store.put("café".encode()) == store.put("café".encode())


def test_same_approved_transcript_bytes_have_same_artifact_ref(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    text = "There is a subtle difference."
    assert store.put(text.encode()) == store.put(text.encode())


def test_two_audio_attempts_may_share_approved_transcript_ref(
    tmp_path: Path,
) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    first_receipt = build_receipt(first, make_stage(first))
    second_receipt = build_receipt(second, make_stage(second))
    append_receipt(first, first_receipt)
    append_receipt(second, second_receipt)
    assert first_receipt.transcription["approved_transcript_ref"] == (
        second_receipt.transcription["approved_transcript_ref"]
    )
    assert first_receipt.attempt_id != second_receipt.attempt_id
    assert len(read_transcription_ledger(first.transcription_path)) == 2


def test_unicode_equivalent_transcript_bytes_have_distinct_refs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    assert store.put("café".encode()) != store.put("cafe\u0301".encode())


def test_trailing_newline_is_preserved_in_approved_transcript(tmp_path: Path) -> None:
    approved = "There is a subtle difference.\n"
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, approved=approved)
    evidence = load_transcription(runtime)
    assert evidence.approved_transcript_text == approved
    assert evidence.approved_transcript_ref == (
        "sha256:" + hashlib.sha256(approved.encode()).hexdigest()
    )


@pytest.mark.parametrize(
    ("artifact_name", "corruption"),
    (
        ("stt_output_ref", "missing"),
        ("stt_output_ref", "changed"),
        ("approved_transcript_ref", "missing"),
        ("approved_transcript_ref", "changed"),
    ),
)
def test_referenced_transcript_artifacts_must_verify(
    tmp_path: Path,
    artifact_name: str,
    corruption: str,
) -> None:
    runtime = make_runtime(tmp_path)
    stage = make_stage(
        runtime,
        candidate="candidate transcript",
        approved="approved subtle transcript",
    )
    path = runtime.store.root / str(stage[artifact_name]).removeprefix("sha256:")
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with pytest.raises(TranscriptionLedgerError, match="missing or corrupt"):
        append_receipt(runtime, build_receipt(runtime, stage))


@pytest.mark.parametrize("artifact_name", ("stt_output_ref", "approved_transcript_ref"))
def test_referenced_transcript_artifacts_require_strict_utf8(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    runtime = make_runtime(tmp_path)
    stage = make_stage(runtime)
    stage[artifact_name] = runtime.store.put(b"\xff")
    with pytest.raises(TranscriptionLedgerError, match="strict UTF-8"):
        append_receipt(runtime, build_receipt(runtime, stage))


@pytest.mark.parametrize("candidate", ("", " ", "\n\t"))
def test_empty_or_whitespace_candidate_cannot_form_success_or_uncertain(
    tmp_path: Path,
    candidate: str,
) -> None:
    for status in ("SUCCESS", "UNCERTAIN"):
        runtime = make_runtime(tmp_path / status)
        receipt = build_receipt(
            runtime,
            make_stage(runtime, status, candidate=candidate),
        )
        with pytest.raises(TranscriptionLedgerError, match="non-whitespace"):
            append_receipt(runtime, receipt)


@pytest.mark.parametrize(
    ("status", "after_stt", "expected"),
    (
        (
            "SUCCESS",
            False,
            {
                "status",
                "stt_model_id",
                "stt_model_version",
                "decoder_version",
                "stt_output_ref",
                "approved_transcript_ref",
                "verifier_id",
                "verifier_version",
            },
        ),
        (
            "UNCERTAIN",
            False,
            {
                "status",
                "stt_model_id",
                "stt_model_version",
                "decoder_version",
                "stt_output_ref",
                "verifier_id",
                "verifier_version",
                "uncertainty_code",
            },
        ),
        ("FAILED", False, {"status", "failure_code"}),
        (
            "FAILED",
            True,
            {
                "status",
                "stt_model_id",
                "stt_model_version",
                "decoder_version",
                "failure_code",
            },
        ),
    ),
)
def test_transcription_status_union_has_exact_keyset(
    tmp_path: Path,
    status: str,
    after_stt: bool,
    expected: set[str],
) -> None:
    runtime = make_runtime(tmp_path)
    receipt = build_receipt(runtime, make_stage(runtime, status, after_stt=after_stt))
    assert set(receipt.transcription) == expected


def test_unknown_uncertainty_code_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(TranscriptionLedgerError, match="uncertainty_code"):
        build_receipt(
            runtime,
            make_stage(runtime, "UNCERTAIN", uncertainty_code="unknown"),
        )


@pytest.mark.parametrize(
    "failure_code",
    ("verification_not_performed", "stt_invocation_failed", "stt_output_invalid"),
)
def test_unknown_failure_codes_are_rejected(
    tmp_path: Path,
    failure_code: str,
) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(TranscriptionLedgerError, match="failure_code"):
        build_receipt(
            runtime,
            make_stage(runtime, "FAILED", failure_code=failure_code),
        )


def test_boolean_verifier_version_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    stage = make_stage(runtime)
    stage["verifier_version"] = True
    with pytest.raises(TranscriptionLedgerError, match="positive integer"):
        build_receipt(runtime, stage)


# ---------------------------------------------------------------------------
# Sealed evidence, S presence, and S semantic rebinding
# ---------------------------------------------------------------------------


def test_transcription_evidence_a_rejects_attempt_b(tmp_path: Path) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    append_receipt(first, build_receipt(first, make_stage(first)))
    evidence_a = load_transcription(first, first.attempt())
    with pytest.raises(TranscriptionEvidenceError, match="does not bind"):
        speech_module._require_transcription_evidence(  # type: ignore[attr-defined]
            evidence_a,
            attempt=second.attempt(),
        )


def test_same_audio_ref_still_requires_independent_receipts(tmp_path: Path) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    append_receipt(first, build_receipt(first, make_stage(first)))
    with pytest.raises(TranscriptionEvidenceError, match="exactly one"):
        load_transcription(second, second.attempt())
    append_receipt(second, build_receipt(second, make_stage(second)))
    assert load_transcription(second, second.attempt()).attempt_id == second.attempt_id


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("status", "FAILED"),
        ("stt_output_ref", "sha256:" + "0" * 64),
        ("approved_transcript_ref", "sha256:" + "1" * 64),
        ("approved_transcript_text", "Changed transcript"),
        ("verifier_id", "verifier-b"),
        ("verifier_version", 2),
    ),
)
def test_transcription_public_field_mutation_is_rejected(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    evidence = load_transcription(runtime)
    object.__setattr__(evidence, field, replacement)
    with pytest.raises(TranscriptionEvidenceError):
        evidence.to_provenance()


def test_hash_consistent_transcript_text_and_ref_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    evidence = load_transcription(runtime)
    changed = "A subtle alteration was invented by the caller."
    object.__setattr__(evidence, "approved_transcript_text", changed)
    object.__setattr__(
        evidence,
        "approved_transcript_ref",
        "sha256:" + hashlib.sha256(changed.encode()).hexdigest(),
    )
    object.__setattr__(evidence, "_approved_transcript_bytes", changed.encode())
    with pytest.raises(TranscriptionEvidenceError, match="durable receipt"):
        evidence.to_provenance()


def test_only_success_transcription_can_enter_d19(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    assert evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    ).target_present is True


@pytest.mark.parametrize("status", ("UNCERTAIN", "FAILED"))
def test_non_success_transcription_is_rejected_by_presence(
    tmp_path: Path,
    status: str,
) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, status)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    with pytest.raises(PresenceEvidenceError, match="SUCCESS"):
        evaluate_speech_presence_gate(
            attempt=attempt,
            unit=unit,
            transcription=load_transcription(runtime, attempt),
        )


def test_s_presence_source_ref_is_approved_transcript_ref(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    assert gate.source_artifact_ref == evidence.approved_transcript_ref
    assert gate.source_artifact_ref != attempt.response_artifact_ref


def test_s_presence_has_no_caller_target_present_parameter() -> None:
    assert "target_present" not in inspect.signature(
        evaluate_speech_presence_gate
    ).parameters


def test_s_presence_never_decodes_raw_audio_as_utf8(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, raw_audio=b"\xff\xfe\x80\x00")
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    assert evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    ).target_present is True


@pytest.mark.parametrize("replacement", (False, True))
def test_s_presence_target_mutation_is_rejected(
    tmp_path: Path,
    replacement: bool,
) -> None:
    approved = (
        "There is a subtle difference."
        if not replacement
        else "The results were completely different."
    )
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, approved=approved)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    assert gate.target_present is not replacement
    object.__setattr__(gate, "target_present", replacement)
    with pytest.raises(PresenceEvidenceError):
        plan_speech_assessment(
            attempt=attempt,
            unit=unit,
            transcription=evidence,
            presence=gate,
        )


def test_unverified_stt_presence_cannot_reach_d19(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.store.put(b"subtle candidate without receipt")
    with pytest.raises(TranscriptionEvidenceError):
        load_transcription(runtime)


def test_unverified_target_absence_cannot_become_omitted(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(
        runtime,
        "UNCERTAIN",
        candidate="The results were completely different.",
    )
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    assert plan.speak_payload()["outcome"] == "ABSTAIN"
    assert plan.speak_payload()["reason_code"] == "transcription_uncertain"


def test_s_semantic_request_requires_exact_approved_transcript(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    with pytest.raises(SemanticEvidenceError, match="exactly"):
        bind_semantic(
            attempt=attempt,
            unit=unit,
            transcription=evidence,
            presence=gate,
            request_transcript=evidence.approved_transcript_text + " ",
        )


def test_normalization_equivalent_s_transcript_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    approved = "A café has a subtle atmosphere."
    record_disposition(runtime, approved=approved)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    with pytest.raises(SemanticEvidenceError, match="exactly"):
        bind_semantic(
            attempt=attempt,
            unit=unit,
            transcription=evidence,
            presence=gate,
            request_transcript="A cafe\u0301 has a subtle atmosphere.",
        )


def test_s_semantic_bundle_from_attempt_a_rejects_planning_b(tmp_path: Path) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    append_receipt(first, build_receipt(first, make_stage(first)))
    append_receipt(second, build_receipt(second, make_stage(second)))
    unit = validate_unit_evidence(make_unit())
    attempt_a = first.attempt()
    transcription_a = load_transcription(first, attempt_a)
    gate_a = evaluate_speech_presence_gate(
        attempt=attempt_a,
        unit=unit,
        transcription=transcription_a,
    )
    semantic_a, _ = bind_semantic(
        attempt=attempt_a,
        unit=unit,
        transcription=transcription_a,
        presence=gate_a,
    )
    attempt_b = second.attempt()
    transcription_b = load_transcription(second, attempt_b)
    gate_b = evaluate_speech_presence_gate(
        attempt=attempt_b,
        unit=unit,
        transcription=transcription_b,
    )
    with pytest.raises(SemanticEvidenceError):
        plan_speech_assessment(
            attempt=attempt_b,
            unit=unit,
            transcription=transcription_b,
            presence=gate_b,
            semantic=semantic_a,
        )


def test_s_proposal_request_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    request_raw, proposal_raw, review_raw = make_semantic_artifacts(
        transcript=evidence.approved_transcript_text
    )
    proposal = json.loads(proposal_raw)
    proposal["request_digest"] = "0" * 64
    with pytest.raises(ValueError, match="request_digest"):
        bind_t11_semantic_evidence(
            request_raw=request_raw,
            proposal_raw=canonical_json_bytes(proposal),
            review_raw=review_raw,
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
            presence=gate,
            transcription=evidence,
        )


def test_s_review_proposal_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    request_raw, proposal_raw, review_raw = make_semantic_artifacts(
        transcript=evidence.approved_transcript_text
    )
    review = json.loads(review_raw)
    review["response_digest"] = "0" * 64
    with pytest.raises(ValueError, match="response_digest"):
        bind_t11_semantic_evidence(
            request_raw=request_raw,
            proposal_raw=proposal_raw,
            review_raw=canonical_json_bytes(review),
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
            presence=gate,
            transcription=evidence,
        )


def test_identical_t11_artifacts_can_be_independently_rebound(
    tmp_path: Path,
) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    append_receipt(first, build_receipt(first, make_stage(first)))
    append_receipt(second, build_receipt(second, make_stage(second)))
    unit = validate_unit_evidence(make_unit())
    attempt_a = first.attempt()
    attempt_b = second.attempt()
    trans_a = load_transcription(first, attempt_a)
    trans_b = load_transcription(second, attempt_b)
    gate_a = evaluate_speech_presence_gate(
        attempt=attempt_a,
        unit=unit,
        transcription=trans_a,
    )
    gate_b = evaluate_speech_presence_gate(
        attempt=attempt_b,
        unit=unit,
        transcription=trans_b,
    )
    artifacts = make_semantic_artifacts(transcript=trans_a.approved_transcript_text)
    bundles = []
    for attempt, transcription, gate in (
        (attempt_a, trans_a, gate_a),
        (attempt_b, trans_b, gate_b),
    ):
        bundles.append(
            bind_t11_semantic_evidence(
                request_raw=artifacts[0],
                proposal_raw=artifacts[1],
                review_raw=artifacts[2],
                assessor_id=ASSESSOR_ID,
                assessor_version=ASSESSOR_VERSION,
                attempt=attempt,
                unit=unit,
                presence=gate,
                transcription=transcription,
            )
        )
    assert bundles[0].attempt_id != bundles[1].attempt_id


# ---------------------------------------------------------------------------
# Exact outcomes, companion closure, audio locator, and sealed plan
# ---------------------------------------------------------------------------


def test_success_target_absent_plans_exact_omitted_pair(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, approved="The results were completely different.")
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    gate = evaluate_speech_presence_gate(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
        presence=gate,
    )
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["outcome"] == judge["outcome"] == "OMITTED"
    assert speak["reason_code"] == judge["reason_code"] == "target_absent"
    assert set(speak["provenance"]) == {"transcription", "presence_gate"}
    assert set(judge).isdisjoint({"assessment_id", "stimulus_ref", "novel"})


def test_success_present_pass_plans_exact_pair(tmp_path: Path) -> None:
    plan, _, attempt, _, _, _ = planned_success(tmp_path)
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["outcome"] == judge["outcome"] == "PASS"
    assert speak["passed"] is judge["passed"] is True
    assert judge["assessment_id"] == attempt.attempt_id
    assert judge["stimulus_ref"] == attempt.presented_stimulus_ref
    assert judge["novel"] is True


@pytest.mark.parametrize(
    "failure_code",
    ("semantic_misuse", "collocation_misuse", "form_misuse"),
)
def test_success_present_fail_plans_exact_pair(
    tmp_path: Path,
    failure_code: str,
) -> None:
    plan, _, _, _, _, _ = planned_success(
        tmp_path,
        outcome="FAIL",
        failure_code=failure_code,
    )
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["outcome"] == judge["outcome"] == "FAIL"
    assert speak["failure_code"] == judge["failure_code"] == failure_code
    assert judge["assessment_id"] == judge["attempt_id"]


def test_success_semantic_abstain_plans_exact_pair(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(
        tmp_path,
        outcome="ABSTAIN",
        reason_code="semantic_uncertainty",
    )
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["reason_code"] == judge["reason_code"] == "semantic_uncertainty"
    assert speak["authority_kind"] == judge["authority_kind"] == "policy"
    assert set(speak["provenance"]) == {
        "transcription",
        "presence_gate",
        "semantic_judge",
        "human_review",
        "policy",
    }
    assert set(judge).isdisjoint({"assessment_id", "stimulus_ref", "novel"})


def test_success_reviewer_reject_plans_exact_pair(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path, decision="REJECT")
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["reason_code"] == judge["reason_code"] == "reviewer_rejected"
    assert speak["provenance"]["human_review"]["decision"] == "REJECT"
    assert set(judge).isdisjoint({"assessment_id", "stimulus_ref", "novel"})


def test_uncertain_plans_exact_abstain_pair(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, "UNCERTAIN")
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["transcript"] == ""
    assert speak["reason_code"] == judge["reason_code"] == "transcription_uncertain"
    assert set(speak["provenance"]) == {"transcription", "policy"}


@pytest.mark.parametrize(
    "failure_code",
    ("transcription_failed", "audio_unusable", "infrastructure_failure"),
)
def test_failed_plans_exact_abstain_pair(
    tmp_path: Path,
    failure_code: str,
) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, "FAILED", failure_code=failure_code)
    attempt = runtime.attempt()
    unit = validate_unit_evidence(make_unit())
    evidence = load_transcription(runtime, attempt)
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=unit,
        transcription=evidence,
    )
    speak, judge = plan.speak_payload(), plan.judge_payload()
    assert speak["transcript"] == ""
    assert speak["reason_code"] == judge["reason_code"] == failure_code
    assert set(judge).isdisjoint({"assessment_id", "stimulus_ref", "novel"})


def test_no_public_standalone_speak_or_speech_judge_api() -> None:
    assert not hasattr(speech_module, "PlannedSpeak")
    assert not hasattr(speech_module, "plan_speak")
    assert not hasattr(speech_module, "plan_speech_judge")
    assert "audio_path" not in inspect.signature(plan_speech_assessment).parameters


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("attempt_id", "attempt:v1:" + "0" * 64),
        ("response_audio_ref", "sha256:" + "0" * 64),
        ("outcome", "FAIL"),
        ("authority_kind", "policy"),
        ("provenance", {"transcription": {}}),
        ("failure_code", "semantic_misuse"),
    ),
)
def test_companion_mismatch_is_rejected(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    speak = plan.speak_payload()
    judge = plan.judge_payload()
    if field == "response_audio_ref":
        speak[field] = replacement
    elif field == "failure_code":
        speak[field] = replacement
    else:
        judge[field] = replacement
    with pytest.raises(SpeechPlanningError):
        speech_module._validate_companion_pair(  # type: ignore[attr-defined]
            unit_key=plan.unit_key,
            response_audio_ref=plan.response_audio_ref,
            speak=speak,
            judge=judge,
        )


def test_speak_never_contains_d35(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    assert set(plan.speak_payload()).isdisjoint(
        {"assessment_id", "stimulus_ref", "novel"}
    )


@pytest.mark.parametrize("status", ("UNCERTAIN", "FAILED"))
def test_abstain_judge_has_zero_d35(tmp_path: Path, status: str) -> None:
    runtime = make_runtime(tmp_path)
    record_disposition(runtime, status)
    attempt = runtime.attempt()
    plan = plan_speech_assessment(
        attempt=attempt,
        unit=validate_unit_evidence(make_unit()),
        transcription=load_transcription(runtime, attempt),
    )
    assert set(plan.judge_payload()).isdisjoint(
        {"assessment_id", "stimulus_ref", "novel"}
    )


@pytest.mark.parametrize("outcome", ("PASS", "FAIL"))
def test_pass_fail_judge_has_complete_d35(tmp_path: Path, outcome: str) -> None:
    plan, _, _, _, _, _ = planned_success(
        tmp_path,
        outcome=outcome,
        failure_code="semantic_misuse" if outcome == "FAIL" else "",
    )
    assert {"assessment_id", "stimulus_ref", "novel"}.issubset(
        plan.judge_payload()
    )


def test_audio_path_is_exact_response_digest_suffix(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    speak = plan.speak_payload()
    assert speak["audio_path"] == speak["response_audio_ref"].removeprefix("sha256:")
    assert len(speak["audio_path"]) == 64


def test_caller_cannot_supply_audio_path() -> None:
    assert "audio_path" not in inspect.signature(plan_speech_assessment).parameters


def test_artifact_store_root_does_not_change_audio_path(tmp_path: Path) -> None:
    first, _, _, _, _, _ = planned_success(tmp_path / "first")
    second, _, _, _, _, _ = planned_success(tmp_path / "second")
    assert first.response_audio_ref == second.response_audio_ref
    assert first.speak_payload()["audio_path"] == second.speak_payload()["audio_path"]


@pytest.mark.parametrize("half", ("speak", "judge"))
def test_mutating_canonical_half_bytes_is_rejected(
    tmp_path: Path,
    half: str,
) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    field = f"_canonical_{half}_payload_bytes"
    object.__setattr__(plan, field, b"{}")
    with pytest.raises(SpeechPlanningError):
        plan.speak_payload()
    with pytest.raises(SpeechPlanningError):
        plan.judge_payload()


def test_mutating_plan_unit_key_is_rejected(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    object.__setattr__(plan, "unit_key", "subtle::other-sense")
    with pytest.raises(SpeechPlanningError):
        plan.speak_payload()


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("attempt_id", "attempt:v1:" + "0" * 64),
        ("response_audio_ref", "sha256:" + "0" * 64),
    ),
)
def test_mutating_plan_pair_identity_is_rejected(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    object.__setattr__(plan, field, replacement)
    with pytest.raises(SpeechPlanningError):
        plan.speak_payload()


def test_mutating_pair_snapshot_is_rejected(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    object.__setattr__(plan, "_snapshot_bytes", b"{}")
    with pytest.raises(SpeechPlanningError, match="snapshot"):
        plan.judge_payload()


def test_detached_payload_mutation_does_not_change_plan(tmp_path: Path) -> None:
    plan, _, _, _, _, _ = planned_success(tmp_path)
    before_speak = plan.canonical_speak_payload_bytes
    before_judge = plan.canonical_judge_payload_bytes
    speak, judge = plan.speak_payload(), plan.judge_payload()
    speak["outcome"] = "FAIL"
    speak["provenance"]["semantic_judge"]["assessor_id"] = "changed"
    judge["novel"] = False
    assert plan.canonical_speak_payload_bytes == before_speak
    assert plan.canonical_judge_payload_bytes == before_judge


def test_no_audio_has_no_synthetic_speech_plan(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, capture=False)
    with pytest.raises(ValueError, match="capture receipt"):
        runtime.attempt()
    assert "no_audio" not in inspect.signature(plan_speech_assessment).parameters


def test_t12_2b_modules_do_not_import_events_reconcile_or_anki() -> None:
    root = Path(__file__).parents[1] / "vocab"
    prohibited = {"events", "reconcile", "anki"}
    for name in (
        "transcription_ledger.py",
        "transcription_evidence.py",
        "presence_evidence.py",
        "semantic_evidence.py",
        "speech_planning.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imports = {
            node.module.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert imports.isdisjoint(prohibited)


def test_no_t12_2b_module_calls_eventlog_log() -> None:
    root = Path(__file__).parents[1] / "vocab"
    for name in (
        "transcription_ledger.py",
        "transcription_evidence.py",
        "presence_evidence.py",
        "semantic_evidence.py",
        "speech_planning.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        assert [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "log"
        ] == []


def test_no_stt_engine_or_cloud_api_implementation() -> None:
    root = Path(__file__).parents[1] / "vocab"
    for name in (
        "transcription_ledger.py",
        "transcription_evidence.py",
        "speech_planning.py",
    ):
        text = (root / name).read_text(encoding="utf-8")
        assert "requests" not in text
        assert "openai" not in text.lower()
        assert "azure" not in text.lower()
        assert "microphone" not in text.lower()


def test_s_presence_source_contains_no_raw_audio_utf8_decode() -> None:
    root = Path(__file__).parents[1] / "vocab"
    source = (root / "presence_evidence.py").read_text(encoding="utf-8")
    speech_function = source.split("def _evaluate_speech_target_presence", 1)[1]
    assert "response_bytes" not in speech_function
    assert ".decode(" not in speech_function


def test_no_standalone_speech_judge_planning_bypass() -> None:
    public_functions = {
        name
        for name, value in vars(speech_module).items()
        if inspect.isfunction(value)
        and value.__module__ == speech_module.__name__
        and not name.startswith("_")
    }
    assert public_functions == {"plan_speech_assessment"}
