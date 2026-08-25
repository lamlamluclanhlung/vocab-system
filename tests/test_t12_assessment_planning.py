"""Adversarial D63/D64 tests for attempt-bound captured-text planning."""

from __future__ import annotations

import ast
import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

import vocab.assessment_planning as planning_module
from vocab.artifact_json import canonical_json_bytes
from vocab.artifact_store import ArtifactStore, ArtifactStoreError
from vocab.assessment_evidence import (
    AssessmentEvidenceError,
    ValidatedAttemptEvidence,
    load_validated_attempt_evidence,
    validate_unit_evidence,
)
from vocab.assessment_identity import assessment_attempt_id, cognitive_stimulus_ref
from vocab.assessment_planning import (
    AssessmentPlanningError,
    PlannedJudge,
    plan_text_judge,
)
from vocab.capture_ledger import CaptureLedgerError
from vocab.exposure import reserve_exposure
from vocab.human_review import build_human_review, serialize_human_review
from vocab.models import T11AssessmentResult, VocabUnit
from vocab.presence_evidence import (
    PresenceEvidenceError,
    evaluate_presence_gate,
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
    SessionManifestError,
    create_session_manifest,
    persist_session_manifest,
)


UNIT_KEY = "subtle::small-difference"
DEFINITION = "not immediately obvious or easy to notice"
ASSESSOR_ID = "GPT-5.6"
ASSESSOR_VERSION = "version-unavailable-from-ui"
CREATED_AT = "2026-08-25T01:00:00+00:00"
RESERVED_AT = "2026-08-25T01:01:00+00:00"
CAPTURED_AT = "2026-08-25T01:02:00+00:00"


STIMULUS_BY_CHANNEL = {
    "R": {
        "passage": "The distinction between the proposals was subtle.",
        "question": "How was the distinction described?",
    },
    "L": {
        "spoken_script": "The distinction between the proposals was subtle.",
        "question": "How was the distinction described?",
    },
    "W": {
        "production_prompt": "Compare two nearly identical research results.",
        "semantic_constraints": "Use subtle for a small hard-to-notice difference.",
    },
}
TASK_KIND_BY_CHANNEL = {
    "R": "reading_comprehension",
    "L": "listening_comprehension",
    "W": "written_production",
}
DEFAULT_RESPONSE = {
    "R": "It was a slight difference that was hard to notice.",
    "L": "The difference was slight and difficult to notice.",
    "W": "The two studies showed a subtle difference in timing.",
}


@dataclass
class Runtime:
    store: ArtifactStore
    exposure_path: Path
    capture_path: Path
    session_root: Path
    manifest: SessionManifest
    item: dict[str, object]
    attempt_id: str
    response_bytes: bytes

    def evidence(self) -> ValidatedAttemptEvidence:
        return load_validated_attempt_evidence(
            exposure_path=self.exposure_path,
            capture_path=self.capture_path,
            artifact_store=self.store,
            session_root=self.session_root,
            attempt_id=self.attempt_id,
        )


def make_unit(
    channel: str,
    *,
    lemma: str = "subtle",
    unit_type: str = "word",
    definition_en: str = DEFINITION,
    enabled_channel: str | None = None,
) -> VocabUnit:
    target_channel = channel if enabled_channel is None else enabled_channel
    values: dict[str, object] = {
        "unit_key": UNIT_KEY,
        "lemma": lemma,
        "lemma_slug": "subtle",
        "sense_slug": "small-difference",
        "unit_type": unit_type,
        "register": "neutral",
        "definition_en": definition_en,
        "source_ref": "dictionary:cambridge:subtle",
        "source_sentence": f"The example contains {lemma} in context.",
    }
    values[f"Target_{target_channel}"] = "1"
    values[f"state_{target_channel}"] = "NEW"
    return VocabUnit(**values)  # type: ignore[arg-type]


def make_item(
    store: ArtifactStore,
    channel: str,
    *,
    stimulus: dict[str, str] | None = None,
) -> dict[str, object]:
    selected = dict(STIMULUS_BY_CHANNEL[channel] if stimulus is None else stimulus)
    stimulus_ref = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel=channel,
        task_kind=TASK_KIND_BY_CHANNEL[channel],
        stimulus=selected,
    )
    return {
        "item_ordinal": 0,
        "unit_key": UNIT_KEY,
        "channel": channel,
        "task_kind": TASK_KIND_BY_CHANNEL[channel],
        "stimulus": selected,
        "presented_stimulus_ref": stimulus_ref,
        "stimulus_artifact_ref": store.put(canonical_json_bytes(selected)),
    }


def make_runtime(
    tmp_path: Path,
    channel: str = "R",
    *,
    response_bytes: bytes | None = None,
    reserve: bool = True,
    capture: bool = True,
) -> Runtime:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    session_root = tmp_path / "sessions"
    initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        artifact_store=store,
        no_historical_t12_state=True,
    )
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
    selected_response = (
        DEFAULT_RESPONSE[channel].encode("utf-8")
        if response_bytes is None
        else response_bytes
    )
    if reserve:
        permit = reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
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
                artifact_store=store,
                captured_at=CAPTURED_AT,
                display_permit=permit,
                response_bytes=selected_response,
            )
    return Runtime(
        store=store,
        exposure_path=exposure_path,
        capture_path=capture_path,
        session_root=session_root,
        manifest=manifest,
        item=item,
        attempt_id=attempt_id,
        response_bytes=selected_response,
    )


def append_attempt(runtime: Runtime) -> Runtime:
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
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=runtime.response_bytes,
    )
    return Runtime(
        store=runtime.store,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        session_root=runtime.session_root,
        manifest=manifest,
        item=runtime.item,
        attempt_id=attempt_id,
        response_bytes=runtime.response_bytes,
    )


def make_artifacts(
    *,
    attempt: ValidatedAttemptEvidence,
    unit: object,
    learner_response: str | None = None,
    stimulus: dict[str, str] | None = None,
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
) -> tuple[bytes, bytes, bytes, dict[str, object], dict[str, object], dict[str, object]]:
    selected_stimulus = dict(
        STIMULUS_BY_CHANNEL[attempt.channel] if stimulus is None else stimulus
    )
    response = (
        attempt.response_bytes.decode("utf-8")
        if learner_response is None
        else learner_response
    )
    task_content = {**selected_stimulus, "learner_response": response}
    request = build_semantic_request(
        unit_key=unit.unit_key,
        lemma=unit.lemma,
        unit_type=unit.unit_type,
        definition_en=unit.definition_en,
        channel=attempt.channel,
        task_content=task_content,
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
    return (
        serialize_semantic_request(request),
        proposal_raw,
        review_raw,
        request,
        proposal,
        review,
    )


def bind(
    attempt: ValidatedAttemptEvidence,
    unit: object,
    *,
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
    learner_response: str | None = None,
    stimulus: dict[str, str] | None = None,
) -> tuple[object, object | None, tuple[bytes, bytes, bytes, dict[str, object], dict[str, object], dict[str, object]]]:
    presence = (
        evaluate_presence_gate(attempt=attempt, unit=unit)
        if attempt.channel == "W"
        else None
    )
    artifacts = make_artifacts(
        attempt=attempt,
        unit=unit,
        learner_response=learner_response,
        stimulus=stimulus,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
        decision=decision,
    )
    request_raw, proposal_raw, review_raw, *_ = artifacts
    bundle = bind_t11_semantic_evidence(
        request_raw=request_raw,
        proposal_raw=proposal_raw,
        review_raw=review_raw,
        assessor_id=ASSESSOR_ID,
        assessor_version=ASSESSOR_VERSION,
        attempt=attempt,
        unit=unit,
        presence=presence,
    )
    return bundle, presence, artifacts


def planned(
    tmp_path: Path,
    channel: str = "R",
    *,
    outcome: str = "PASS",
    failure_code: str = "",
    reason_code: str = "",
    decision: str = "APPROVE",
    response_bytes: bytes | None = None,
) -> tuple[PlannedJudge, Runtime, object, object, object | None]:
    runtime = make_runtime(tmp_path, channel, response_bytes=response_bytes)
    attempt = runtime.evidence()
    unit = validate_unit_evidence(make_unit(channel))
    bundle, presence, _ = bind(
        attempt,
        unit,
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
        decision=decision,
    )
    result = plan_text_judge(
        attempt=attempt,
        unit=unit,
        semantic=bundle,
        presence=presence,
    )
    return result, runtime, attempt, bundle, presence


def test_no_exposure_reservation_fails_attempt_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, reserve=False, capture=False)
    with pytest.raises(AssessmentEvidenceError, match="exposure"):
        runtime.evidence()


@pytest.mark.parametrize("corruption", ["missing", "corrupt"])
def test_missing_or_corrupt_persisted_session_fails(
    tmp_path: Path,
    corruption: str,
) -> None:
    runtime = make_runtime(tmp_path)
    path = runtime.session_root / runtime.manifest.session_id.removeprefix("session:v1:")
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{}")
    with pytest.raises(SessionManifestError):
        runtime.evidence()


@pytest.mark.parametrize("corruption", ["missing", "corrupt"])
def test_missing_or_corrupt_stimulus_artifact_fails(
    tmp_path: Path,
    corruption: str,
) -> None:
    runtime = make_runtime(tmp_path)
    path = runtime.store.root / str(runtime.item["stimulus_artifact_ref"]).removeprefix("sha256:")
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with pytest.raises(ArtifactStoreError):
        runtime.evidence()


@pytest.mark.parametrize("corruption", ["missing", "corrupt"])
def test_capture_receipt_response_artifact_must_verify(
    tmp_path: Path,
    corruption: str,
) -> None:
    runtime = make_runtime(tmp_path)
    response_ref = "sha256:" + hashlib.sha256(runtime.response_bytes).hexdigest()
    path = runtime.store.root / response_ref.removeprefix("sha256:")
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with pytest.raises(CaptureLedgerError):
        runtime.evidence()


def test_novelty_is_ledger_derived_and_not_a_planner_parameter(tmp_path: Path) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    second_attempt = second.evidence()
    unit = validate_unit_evidence(make_unit("R"))
    bundle, _, _ = bind(second_attempt, unit)
    judge = plan_text_judge(attempt=second_attempt, unit=unit, semantic=bundle)
    assert judge.to_payload()["novel"] is False
    assert "novel" not in inspect.signature(plan_text_judge).parameters


def test_missing_capture_receipt_fails_attempt_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, capture=False)
    with pytest.raises(AssessmentEvidenceError, match="capture receipt"):
        runtime.evidence()


@pytest.mark.parametrize(
    ("field", "replacement", "replacement_type"),
    [
        ("lemma", "delicate", "word"),
        ("unit_type", "subtle difference", "chunk"),
        ("definition_en", "a different approved sense", "word"),
    ],
)
def test_semantic_unit_block_must_match_validated_unit_snapshot(
    tmp_path: Path,
    field: str,
    replacement: str,
    replacement_type: str,
) -> None:
    runtime = make_runtime(tmp_path)
    attempt = runtime.evidence()
    unit = validate_unit_evidence(make_unit("R"))
    request_unit = validate_unit_evidence(
        make_unit(
            "R",
            lemma=replacement if field in ("lemma", "unit_type") else "subtle",
            unit_type=replacement_type,
            definition_en=replacement if field == "definition_en" else DEFINITION,
        )
    )
    artifacts = make_artifacts(attempt=attempt, unit=request_unit)
    with pytest.raises(SemanticEvidenceError, match="Unit block"):
        bind_t11_semantic_evidence(
            request_raw=artifacts[0],
            proposal_raw=artifacts[1],
            review_raw=artifacts[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
        )


def test_attempt_channel_must_be_enabled_in_validated_unit(tmp_path: Path) -> None:
    attempt = make_runtime(tmp_path, "R").evidence()
    unit = validate_unit_evidence(make_unit("R", enabled_channel="L"))
    with pytest.raises(AssessmentEvidenceError, match="not enabled"):
        bind(attempt, unit)


def test_attempt_a_semantics_reject_attempt_b_with_different_response(
    tmp_path: Path,
) -> None:
    attempt_a = make_runtime(tmp_path / "a", response_bytes=b"response A").evidence()
    attempt_b = make_runtime(tmp_path / "b", response_bytes=b"response B").evidence()
    unit = validate_unit_evidence(make_unit("R"))
    artifacts = make_artifacts(attempt=attempt_a, unit=unit)
    with pytest.raises(SemanticEvidenceError, match="learner_response"):
        bind_t11_semantic_evidence(
            request_raw=artifacts[0],
            proposal_raw=artifacts[1],
            review_raw=artifacts[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt_b,
            unit=unit,
        )


@pytest.mark.parametrize(
    ("captured", "request_text"),
    [
        ("café", "cafè"),
        ("café", "cafe\u0301"),
        ("exact response ", "exact response"),
        ("line one\r\nline two", "line one\nline two"),
    ],
)
def test_captured_text_binding_is_exact_without_normalization(
    tmp_path: Path,
    captured: str,
    request_text: str,
) -> None:
    attempt = make_runtime(
        tmp_path,
        response_bytes=captured.encode("utf-8"),
    ).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    artifacts = make_artifacts(
        attempt=attempt,
        unit=unit,
        learner_response=request_text,
    )
    with pytest.raises(SemanticEvidenceError, match="exactly"):
        bind_t11_semantic_evidence(
            request_raw=artifacts[0],
            proposal_raw=artifacts[1],
            review_raw=artifacts[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
        )


def test_invalid_utf8_capture_fails_semantic_binding_not_as_learner_fail(
    tmp_path: Path,
) -> None:
    attempt = make_runtime(tmp_path, response_bytes=b"\xff").evidence()
    valid_attempt = make_runtime(
        tmp_path / "valid",
        response_bytes=b"valid response",
    ).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    artifacts = make_artifacts(
        attempt=valid_attempt,
        unit=unit,
    )
    with pytest.raises(SemanticEvidenceError, match="strict UTF-8"):
        bind_t11_semantic_evidence(
            request_raw=artifacts[0],
            proposal_raw=artifacts[1],
            review_raw=artifacts[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
        )


def test_d54_whitespace_only_stimulus_difference_is_accepted(tmp_path: Path) -> None:
    attempt = make_runtime(tmp_path).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    changed = dict(STIMULUS_BY_CHANNEL["R"])
    changed["passage"] = "  The distinction\r\nbetween\tthe proposals was subtle.  "
    bundle, _, _ = bind(attempt, unit, stimulus=changed)
    assert bundle.attempt_id == attempt.attempt_id


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("passage", "The distinction between the proposals was subtle!"),
        ("passage", "the distinction between the proposals was subtle."),
        ("question", "What did subtle mean here?"),
    ],
)
def test_stimulus_identity_changes_fail_binding(
    tmp_path: Path,
    field: str,
    changed: str,
) -> None:
    attempt = make_runtime(tmp_path).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    stimulus = dict(STIMULUS_BY_CHANNEL["R"])
    stimulus[field] = changed
    with pytest.raises(SemanticEvidenceError, match="cognitive stimulus"):
        bind(attempt, unit, stimulus=stimulus)


def test_content_identical_t11_bytes_can_be_independently_rebound(
    tmp_path: Path,
) -> None:
    first_runtime = make_runtime(tmp_path)
    second_runtime = append_attempt(first_runtime)
    attempt_a = first_runtime.evidence()
    attempt_b = second_runtime.evidence()
    unit = validate_unit_evidence(make_unit("R"))
    artifacts = make_artifacts(attempt=attempt_a, unit=unit)
    bundles = []
    for attempt in (attempt_a, attempt_b):
        bundles.append(
            bind_t11_semantic_evidence(
                request_raw=artifacts[0],
                proposal_raw=artifacts[1],
                review_raw=artifacts[2],
                assessor_id=ASSESSOR_ID,
                assessor_version=ASSESSOR_VERSION,
                attempt=attempt,
                unit=unit,
            )
        )
    assert bundles[0].attempt_id == attempt_a.attempt_id
    assert bundles[1].attempt_id == attempt_b.attempt_id
    with pytest.raises(SemanticEvidenceError, match="runtime fields"):
        plan_text_judge(attempt=attempt_b, unit=unit, semantic=bundles[0])
    assert plan_text_judge(
        attempt=attempt_b,
        unit=unit,
        semantic=bundles[1],
    ).to_payload()["novel"] is False


def test_proposal_bound_to_another_request_is_rejected(tmp_path: Path) -> None:
    attempt = make_runtime(tmp_path).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    first = make_artifacts(attempt=attempt, unit=unit)
    changed = make_artifacts(
        attempt=attempt,
        unit=unit,
        learner_response=attempt.response_bytes.decode() + "!",
    )
    with pytest.raises(ValueError, match="request_digest"):
        bind_t11_semantic_evidence(
            request_raw=first[0],
            proposal_raw=changed[1],
            review_raw=changed[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
        )


def test_review_bound_to_another_proposal_is_rejected(tmp_path: Path) -> None:
    attempt = make_runtime(tmp_path).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    first = make_artifacts(attempt=attempt, unit=unit)
    second = make_artifacts(
        attempt=attempt,
        unit=unit,
        outcome="ABSTAIN",
        reason_code="semantic_uncertainty",
    )
    with pytest.raises(ValueError, match="response_digest"):
        bind_t11_semantic_evidence(
            request_raw=first[0],
            proposal_raw=first[1],
            review_raw=second[2],
            assessor_id=ASSESSOR_ID,
            assessor_version=ASSESSOR_VERSION,
            attempt=attempt,
            unit=unit,
        )


def test_no_separately_supplied_t11_result_api_exists(tmp_path: Path) -> None:
    assert "assessment_result" not in inspect.signature(
        bind_t11_semantic_evidence
    ).parameters
    assert "assessment_result" not in inspect.signature(plan_text_judge).parameters
    with pytest.raises(TypeError):
        plan_text_judge(  # type: ignore[call-arg]
            attempt=make_runtime(tmp_path).evidence(),
            unit=validate_unit_evidence(make_unit("R")),
            assessment_result=T11AssessmentResult(UNIT_KEY, "R", "PASS"),
        )


def test_runtime_bundle_mutation_cannot_bypass_independent_materialization(
    tmp_path: Path,
) -> None:
    _, runtime, attempt, bundle, _ = planned(tmp_path)
    unit = validate_unit_evidence(make_unit("R"))
    object.__setattr__(
        bundle,
        "assessment_result",
        T11AssessmentResult(UNIT_KEY, "R", "FAIL", "wrong_meaning"),
    )
    with pytest.raises(SemanticEvidenceError, match="runtime fields"):
        plan_text_judge(attempt=attempt, unit=unit, semantic=bundle)
    assert runtime.response_bytes


def test_w_presence_is_computed_internally_with_d19(tmp_path: Path) -> None:
    present = make_runtime(
        tmp_path / "present",
        "W",
        response_bytes=b"A subtle difference remained.",
    ).evidence()
    absent = make_runtime(
        tmp_path / "absent",
        "W",
        response_bytes=b"A large difference remained.",
    ).evidence()
    unit = validate_unit_evidence(make_unit("W"))
    assert evaluate_presence_gate(attempt=present, unit=unit).target_present is True
    assert evaluate_presence_gate(attempt=absent, unit=unit).target_present is False
    assert "target_present" not in inspect.signature(evaluate_presence_gate).parameters


def test_presence_evidence_from_another_attempt_or_source_is_rejected(
    tmp_path: Path,
) -> None:
    first = make_runtime(tmp_path)
    second = append_attempt(first)
    attempt_a = first.evidence()
    attempt_b = second.evidence()
    unit = validate_unit_evidence(make_unit("R"))
    w_unit = validate_unit_evidence(make_unit("W"))
    w_attempt = make_runtime(tmp_path / "w", "W").evidence()
    gate = evaluate_presence_gate(attempt=w_attempt, unit=w_unit)
    object.__setattr__(gate, "attempt_id", attempt_a.attempt_id)
    object.__setattr__(gate, "source_artifact_ref", attempt_b.response_artifact_ref)
    with pytest.raises(PresenceEvidenceError, match="does not bind"):
        planning_module._require_presence_evidence(  # type: ignore[attr-defined]
            gate,
            attempt=w_attempt,
            unit=w_unit,
        )
    assert unit.unit_key == w_unit.unit_key


def test_w_target_absent_plans_exact_omitted_payload(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        "W",
        response_bytes=b"The results were dramatically different.",
    )
    attempt = runtime.evidence()
    unit = validate_unit_evidence(make_unit("W"))
    gate = evaluate_presence_gate(attempt=attempt, unit=unit)
    judge = plan_text_judge(attempt=attempt, unit=unit, presence=gate)
    payload = judge.to_payload()
    assert payload["outcome"] == "OMITTED"
    assert payload["reason_code"] == "target_absent"
    assert payload["authority_kind"] == "deterministic_gate"
    assert payload["model_id"] == "d19-target-presence"
    assert payload["model_version"] == "1"
    assert set(payload["provenance"]) == {"presence_gate"}
    assert set(payload).isdisjoint({"assessment_id", "stimulus_ref", "novel", "failure_code"})


def test_w_target_absent_forbids_semantic_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        "W",
        response_bytes=b"The results were dramatically different.",
    )
    attempt = runtime.evidence()
    unit = validate_unit_evidence(make_unit("W"))
    gate = evaluate_presence_gate(attempt=attempt, unit=unit)
    with pytest.raises(SemanticEvidenceError, match="forbids"):
        bind(attempt, unit)
    fake_semantic = object.__new__(planning_module.T11SemanticEvidenceBundle)
    with pytest.raises(AssessmentPlanningError, match="forbids"):
        plan_text_judge(
            attempt=attempt,
            unit=unit,
            presence=gate,
            semantic=fake_semantic,
        )


def test_w_target_present_requires_semantic_evidence(tmp_path: Path) -> None:
    attempt = make_runtime(tmp_path, "W").evidence()
    unit = validate_unit_evidence(make_unit("W"))
    gate = evaluate_presence_gate(attempt=attempt, unit=unit)
    assert gate.target_present is True
    with pytest.raises(AssessmentPlanningError, match="requires semantic"):
        plan_text_judge(attempt=attempt, unit=unit, presence=gate)


@pytest.mark.parametrize("channel", ["R", "L"])
def test_r_l_never_accept_presence_evidence(tmp_path: Path, channel: str) -> None:
    attempt = make_runtime(tmp_path / channel, channel).evidence()
    unit = validate_unit_evidence(make_unit(channel))
    bundle, _, _ = bind(attempt, unit)
    w_attempt = make_runtime(tmp_path / "w", "W").evidence()
    w_unit = validate_unit_evidence(make_unit("W"))
    gate = evaluate_presence_gate(attempt=w_attempt, unit=w_unit)
    with pytest.raises(AssessmentPlanningError, match="does not accept"):
        plan_text_judge(
            attempt=attempt,
            unit=unit,
            semantic=bundle,
            presence=gate,
        )


@pytest.mark.parametrize("channel", ["R", "L"])
def test_r_l_paraphrase_without_target_can_pass(tmp_path: Path, channel: str) -> None:
    judge, _, _, _, _ = planned(
        tmp_path,
        channel,
        response_bytes=b"It was a small difference that was difficult to notice.",
    )
    assert judge.to_payload()["outcome"] == "PASS"


def test_pass_payload_has_complete_d35_and_semantic_authority(tmp_path: Path) -> None:
    judge, _, attempt, _, _ = planned(tmp_path, "R")
    payload = judge.to_payload()
    assert set(payload) == planning_module._COMMON_FIELDS | planning_module._D35_FIELDS
    assert payload["assessment_id"] == attempt.attempt_id
    assert payload["stimulus_ref"] == attempt.presented_stimulus_ref
    assert payload["novel"] is True
    assert payload["passed"] is True
    assert payload["authority_kind"] == "semantic_model"
    assert set(payload["provenance"]) == {"semantic_judge", "human_review"}


@pytest.mark.parametrize(
    ("channel", "failure_code"),
    [("R", "wrong_meaning"), ("L", "wrong_interpretation"), ("W", "semantic_misuse")],
)
def test_fail_payload_has_channel_code_and_semantic_authority(
    tmp_path: Path,
    channel: str,
    failure_code: str,
) -> None:
    judge, _, _, _, _ = planned(
        tmp_path / channel,
        channel,
        outcome="FAIL",
        failure_code=failure_code,
    )
    payload = judge.to_payload()
    assert payload["outcome"] == "FAIL"
    assert payload["passed"] is False
    assert payload["failure_code"] == failure_code
    assert payload["authority_kind"] == "semantic_model"
    expected = {"semantic_judge", "human_review"}
    if channel == "W":
        expected.add("presence_gate")
    assert set(payload["provenance"]) == expected


def test_semantic_approve_abstain_has_zero_d35_and_all_review_provenance(
    tmp_path: Path,
) -> None:
    judge, _, _, _, _ = planned(
        tmp_path,
        "R",
        outcome="ABSTAIN",
        reason_code="semantic_uncertainty",
    )
    payload = judge.to_payload()
    assert payload["reason_code"] == "semantic_uncertainty"
    assert payload["authority_kind"] == "policy"
    assert set(payload).isdisjoint(planning_module._D35_FIELDS)
    assert set(payload["provenance"]) == {
        "semantic_judge",
        "human_review",
        "policy",
    }
    assert payload["provenance"]["human_review"]["decision"] == "APPROVE"


def test_reviewer_reject_has_zero_d35_and_policy_authority(tmp_path: Path) -> None:
    judge, _, _, _, _ = planned(tmp_path, "W", decision="REJECT")
    payload = judge.to_payload()
    assert payload["outcome"] == "ABSTAIN"
    assert payload["reason_code"] == "reviewer_rejected"
    assert payload["model_id"] == "t12-assessment-policy"
    assert set(payload).isdisjoint(planning_module._D35_FIELDS)
    assert set(payload["provenance"]) == {
        "presence_gate",
        "semantic_judge",
        "human_review",
        "policy",
    }
    assert payload["provenance"]["human_review"]["decision"] == "REJECT"


@pytest.mark.parametrize(
    "mutation",
    ["unknown_stage", "unknown_stage_field", "unknown_payload", "missing_d35", "partial_d35"],
)
def test_payload_and_provenance_closure_rejects_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    judge, _, _, _, _ = planned(tmp_path)
    payload = judge.to_payload()
    if mutation == "unknown_stage":
        payload["provenance"]["unknown"] = {}
    elif mutation == "unknown_stage_field":
        payload["provenance"]["semantic_judge"]["unknown"] = True
    elif mutation == "unknown_payload":
        payload["unknown"] = True
    elif mutation == "missing_d35":
        del payload["novel"]
    else:
        del payload["assessment_id"]
        del payload["stimulus_ref"]
    with pytest.raises(AssessmentPlanningError):
        planning_module._validated_judge_payload(unit_key=UNIT_KEY, payload=payload)


def test_mutating_source_artifact_dictionaries_cannot_change_bundle_or_plan(
    tmp_path: Path,
) -> None:
    attempt = make_runtime(tmp_path).evidence()
    unit = validate_unit_evidence(make_unit("R"))
    artifacts = make_artifacts(attempt=attempt, unit=unit)
    bundle = bind_t11_semantic_evidence(
        request_raw=artifacts[0],
        proposal_raw=artifacts[1],
        review_raw=artifacts[2],
        assessor_id=ASSESSOR_ID,
        assessor_version=ASSESSOR_VERSION,
        attempt=attempt,
        unit=unit,
    )
    before = plan_text_judge(attempt=attempt, unit=unit, semantic=bundle).canonical_payload_bytes
    artifacts[3]["unit"] = {"unit_key": "changed"}
    artifacts[4]["outcome"] = "FAIL"
    artifacts[5]["decision"] = "REJECT"
    after = plan_text_judge(attempt=attempt, unit=unit, semantic=bundle).canonical_payload_bytes
    assert after == before


def test_mutating_original_vocab_unit_cannot_change_snapshot_or_plan(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    attempt = runtime.evidence()
    original = make_unit("R")
    unit = validate_unit_evidence(original)
    bundle, _, _ = bind(attempt, unit)
    original.lemma = "changed"
    original.definition_en = "changed"
    judge = plan_text_judge(attempt=attempt, unit=unit, semantic=bundle)
    assert unit.lemma == "subtle"
    assert judge.unit_key == UNIT_KEY


def test_mutating_returned_payload_copy_cannot_change_planned_judge(tmp_path: Path) -> None:
    judge, _, _, _, _ = planned(tmp_path)
    canonical = judge.canonical_payload_bytes
    detached = judge.to_payload()
    detached["outcome"] = "FAIL"
    detached["provenance"]["semantic_judge"]["assessor_id"] = "changed"
    assert judge.canonical_payload_bytes == canonical
    assert judge.to_payload()["outcome"] == "PASS"
    assert not hasattr(judge, "v")
    assert not hasattr(judge, "ts")
    assert not hasattr(judge, "day")


def test_t12_2a_modules_do_not_import_events_reconcile_or_anki() -> None:
    root = Path(__file__).parents[1] / "vocab"
    prohibited = {"events", "reconcile", "anki"}
    for name in (
        "assessment_evidence.py",
        "semantic_evidence.py",
        "presence_evidence.py",
        "assessment_planning.py",
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


def test_t11_modules_do_not_import_t12_2a_modules() -> None:
    root = Path(__file__).parents[1] / "vocab"
    t12_names = {
        "assessment_evidence",
        "semantic_evidence",
        "presence_evidence",
        "assessment_planning",
    }
    for name in (
        "semantic_request.py",
        "semantic_response.py",
        "human_review.py",
        "review_materialization.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imports = {
            node.module.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert imports.isdisjoint(t12_names)


def test_no_t12_2a_module_calls_eventlog_log() -> None:
    root = Path(__file__).parents[1] / "vocab"
    for name in (
        "assessment_evidence.py",
        "semantic_evidence.py",
        "presence_evidence.py",
        "assessment_planning.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "log"
        ]
        assert calls == []
