"""Adversarial D67 tests for pre-capture terminal text-attempt dispositions."""

from __future__ import annotations

import ast
import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

import vocab.assessment_planning as planning_module
import vocab.response_capture as response_capture_module
from vocab.artifact_json import canonical_json_bytes
from vocab.artifact_store import ArtifactStore, ArtifactStoreError
from vocab.assessment_evidence import (
    AssessmentEvidenceError,
    ValidatedDispositionEvidence,
    load_validated_attempt_evidence,
    load_validated_disposition_evidence,
    validate_unit_evidence,
)
from vocab.assessment_identity import assessment_attempt_id, cognitive_stimulus_ref
from vocab.assessment_planning import (
    AssessmentPlanningError,
    PlannedJudge,
    plan_policy_judge,
)
from vocab.capture_ledger import CaptureLedgerError, CaptureReceipt, read_capture_ledger
from vocab.contracts import (
    ASSESSMENT_ABSTAIN_REASON_CODES,
    ASSESSMENT_OUTCOME_ABSTAIN,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
)
from vocab.disposition_ledger import (
    DISPOSITION_CODES,
    DISPOSITION_LEDGER_VERSION,
    DispositionLedgerError,
    OperationalDispositionReceipt,
    append_disposition_record,
    build_disposition_receipt,
    read_disposition_ledger,
    validate_disposition_bindings,
)
from vocab.exposure import DisplayPermit, ExposureLedgerError, reserve_exposure, validate_t12_histories
from vocab.models import VocabUnit
from vocab.response_capture import (
    capture_response,
    close_text_submission,
    initialize_t12_ledgers,
    record_explicit_skip,
    record_refusal,
    resume_captured_response,
)
from vocab.semantic_response import SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES
from vocab.session import SessionManifest, create_session_manifest, persist_session_manifest


UNIT_KEY = "subtle::small-difference"
DEFINITION = "not immediately obvious or easy to notice"
CREATED_AT = "2026-08-26T03:00:00+00:00"
RESERVED_AT = "2026-08-26T03:01:00+00:00"
CAPTURED_AT = "2026-08-26T03:02:00+00:00"
DISPOSED_AT = "2026-08-26T03:03:00+00:00"


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
    "S": {
        "production_prompt": "Compare two nearly identical research results aloud.",
        "semantic_constraints": "Use subtle for a small hard-to-notice difference.",
    },
}
TASK_KIND_BY_CHANNEL = {
    "R": "reading_comprehension",
    "L": "listening_comprehension",
    "W": "written_production",
    "S": "spoken_production",
}


@dataclass
class Runtime:
    store: ArtifactStore
    exposure_path: Path
    capture_path: Path
    disposition_path: Path
    session_root: Path
    manifest: SessionManifest
    item: dict[str, object]
    attempt_id: str

    def disposition_evidence(self) -> ValidatedDispositionEvidence:
        return load_validated_disposition_evidence(
            exposure_path=self.exposure_path,
            capture_path=self.capture_path,
            disposition_path=self.disposition_path,
            artifact_store=self.store,
            session_root=self.session_root,
            attempt_id=self.attempt_id,
        )


def make_unit(
    channel: str,
    *,
    unit_key: str = UNIT_KEY,
    lemma: str = "subtle",
    lemma_slug: str = "subtle",
    sense_slug: str = "small-difference",
    definition_en: str = DEFINITION,
    enabled_channel: str | None = None,
) -> VocabUnit:
    target_channel = channel if enabled_channel is None else enabled_channel
    values: dict[str, object] = {
        "unit_key": unit_key,
        "lemma": lemma,
        "lemma_slug": lemma_slug,
        "sense_slug": sense_slug,
        "unit_type": "word",
        "register": "neutral",
        "definition_en": definition_en,
        "source_ref": f"dictionary:cambridge:{lemma_slug}",
        "source_sentence": f"The example contains {lemma} in context.",
    }
    values[f"Target_{target_channel}"] = "1"
    values[f"state_{target_channel}"] = "NEW"
    return VocabUnit(**values)  # type: ignore[arg-type]


def make_other_unit(channel: str) -> VocabUnit:
    return make_unit(
        channel,
        unit_key="distinct::other-sense",
        lemma="distinct",
        lemma_slug="distinct",
        sense_slug="other-sense",
        definition_en="clearly different or of a different kind",
    )


def make_item(store: ArtifactStore, channel: str) -> dict[str, object]:
    stimulus = dict(STIMULUS_BY_CHANNEL[channel])
    task_kind = TASK_KIND_BY_CHANNEL[channel]
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


def make_runtime(tmp_path: Path, channel: str) -> tuple[Runtime, DisplayPermit]:
    """Reserve one exact attempt and return it unconsumed, with no capture/disposition yet."""
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    session_root = tmp_path / "sessions"
    initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
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
    runtime = Runtime(
        store=store,
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        session_root=session_root,
        manifest=manifest,
        item=item,
        attempt_id=attempt_id,
    )
    return runtime, permit


def sample_attempt_id(tmp_path: Path, channel: str = "R") -> str:
    """Compute one pattern-valid attempt_id with no durable exposure reservation."""
    store = ArtifactStore(tmp_path / "sample-artifacts")
    item = make_item(store, channel)
    manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    return assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel=channel,
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )


def record_disposition(
    runtime: Runtime,
    permit: DisplayPermit,
    code: str,
) -> CaptureReceipt | OperationalDispositionReceipt:
    """Drive one real production disposition path for each closed D67 code."""
    permit.consume()
    if code == "refusal":
        return record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )
    if code == "explicit_skip":
        return record_explicit_skip(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )
    if code == "no_response":
        return close_text_submission(
            raw_bytes=None,
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    if code == "invalid_artifact":
        return close_text_submission(
            raw_bytes=b"\xff\xfe not valid utf-8",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    if code == "infrastructure_failure":
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                response_capture_module,
                "_append_capture_record",
                fail_capture_commit_before_write,
            )
            return close_text_submission(
                raw_bytes=b"capture-eligible infrastructure fixture",
                display_permit=permit,
                exposure_path=runtime.exposure_path,
                capture_path=runtime.capture_path,
                disposition_path=runtime.disposition_path,
                artifact_store=runtime.store,
                captured_at=CAPTURED_AT,
                disposed_at=DISPOSED_AT,
            )
    raise AssertionError("unsupported helper disposition_code")  # pragma: no cover


def fail_capture_commit_before_write(
    _path: str | Path,
    _record: CaptureReceipt,
) -> None:
    try:
        raise OSError("simulated capture-ledger open failure")
    except OSError as exc:
        raise CaptureLedgerError("capture ledger append failed") from exc


def create_durable_capture_disposition_coexistence(
    runtime: Runtime,
    permit: DisplayPermit,
) -> None:
    """Deliberately bypass the normal disposition writer after real capture."""
    permit.consume()
    capture_response(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"durably captured before deliberate corruption",
    )
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    append_disposition_record(runtime.disposition_path, receipt)
    assert len(read_capture_ledger(runtime.capture_path)) == 1
    assert read_disposition_ledger(runtime.disposition_path) == (receipt,)


# --- disposition_ledger.py: pure schema, closed-code-set, and binding tests ---


def test_disposition_codes_are_members_of_the_frozen_abstain_vocabulary() -> None:
    assert DISPOSITION_CODES <= frozenset(ASSESSMENT_ABSTAIN_REASON_CODES)


def test_disposition_receipt_round_trips_through_the_ledger(tmp_path: Path) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    path = tmp_path / "standalone-dispositions.jsonl"
    path.touch()
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    append_disposition_record(path, receipt)
    assert read_disposition_ledger(path) == (receipt,)
    assert receipt.to_dict() == {
        "v": DISPOSITION_LEDGER_VERSION,
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "disposed_at": DISPOSED_AT,
        "attempt_id": runtime.attempt_id,
        "disposition_code": "refusal",
    }


def test_build_disposition_receipt_rejects_unknown_disposition_code(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    with pytest.raises(DispositionLedgerError, match="invalid disposition_code"):
        build_disposition_receipt(
            disposed_at=DISPOSED_AT,
            attempt_id=runtime.attempt_id,
            disposition_code="reviewer_rejected",
        )


def test_build_disposition_receipt_rejects_invalid_attempt_id() -> None:
    with pytest.raises(DispositionLedgerError, match="invalid attempt_id"):
        build_disposition_receipt(
            disposed_at=DISPOSED_AT,
            attempt_id="not-a-real-attempt-id",
            disposition_code="refusal",
        )


def test_append_disposition_record_detects_duplicate_physical_slot(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    path = tmp_path / "standalone-dispositions.jsonl"
    path.touch()
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    append_disposition_record(path, receipt)
    with pytest.raises(
        DispositionLedgerError, match="duplicate physical disposition slot"
    ):
        append_disposition_record(path, receipt)


def test_malformed_interior_disposition_record_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t12-dispositions.jsonl"
    first = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=sample_attempt_id(tmp_path / "first", "R"),
        disposition_code="refusal",
    )
    second = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=sample_attempt_id(tmp_path / "second", "W"),
        disposition_code="explicit_skip",
    )
    path.write_bytes(
        canonical_json_bytes(first.to_dict())
        + b"\n"
        + b'{"not":"a valid disposition record"}\n'
        + canonical_json_bytes(second.to_dict())
        + b"\n"
    )
    with pytest.raises(DispositionLedgerError, match=r"record 2"):
        read_disposition_ledger(path)


def test_validate_disposition_bindings_rejects_non_receipt_values() -> None:
    with pytest.raises(TypeError, match="non-receipt value"):
        validate_disposition_bindings(
            ({"not": "a receipt"},),  # type: ignore[arg-type]
            exposure_attempt_ids=(),
            exposure_channel_by_attempt_id={},
            capture_attempt_ids=(),
        )


def test_validate_disposition_bindings_requires_exactly_one_exposure(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    with pytest.raises(DispositionLedgerError, match="exactly one compatible"):
        validate_disposition_bindings(
            (receipt,),
            exposure_attempt_ids=(),
            exposure_channel_by_attempt_id={},
            capture_attempt_ids=(),
        )


def test_validate_disposition_bindings_requires_rlw_channel(tmp_path: Path) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    with pytest.raises(DispositionLedgerError, match="non-R/L/W exposure channel"):
        validate_disposition_bindings(
            (receipt,),
            exposure_attempt_ids=(runtime.attempt_id,),
            exposure_channel_by_attempt_id={runtime.attempt_id: "S"},
            capture_attempt_ids=(),
        )


def test_validate_disposition_bindings_rejects_capture_coexistence(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    with pytest.raises(DispositionLedgerError, match="mutual exclusion violated"):
        validate_disposition_bindings(
            (receipt,),
            exposure_attempt_ids=(runtime.attempt_id,),
            exposure_channel_by_attempt_id={runtime.attempt_id: "R"},
            capture_attempt_ids=(runtime.attempt_id,),
        )


def test_disposition_bound_to_s_channel_exposure_is_rejected_by_shared_validation(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "S")
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=runtime.attempt_id,
        disposition_code="refusal",
    )
    append_disposition_record(runtime.disposition_path, receipt)
    with pytest.raises(DispositionLedgerError, match="non-R/L/W exposure channel"):
        validate_t12_histories(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
        )


# --- three-ledger initialization ---


def test_initialize_t12_ledgers_creates_all_three_when_state_confirmed_empty(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    result = initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=True,
    )
    assert result == ((), (), ())
    assert exposure_path.is_file()
    assert capture_path.is_file()
    assert disposition_path.is_file()


def test_initialize_t12_ledgers_creates_only_missing_from_valid_empty_subset(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    exposure_path.touch()
    disposition_path.touch()
    existing_stats = {
        exposure_path: exposure_path.stat(),
        disposition_path: disposition_path.stat(),
    }

    assert initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=True,
    ) == ((), (), ())

    assert capture_path.is_file()
    for path, original in existing_stats.items():
        current = path.stat()
        assert current.st_ino == original.st_ino
        assert current.st_mtime_ns == original.st_mtime_ns


def test_initialize_t12_ledgers_rejects_invalid_present_ledger(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    exposure_path.write_bytes(b'{"malformed":"exposure"}\n')

    with pytest.raises(ExposureLedgerError, match=r"exposure ledger record 1"):
        initialize_t12_ledgers(
            exposure_path=exposure_path,
            capture_path=tmp_path / "t12-captures.jsonl",
            disposition_path=tmp_path / "t12-dispositions.jsonl",
            artifact_store=store,
            no_historical_t12_state=True,
        )


def test_initialize_t12_ledgers_rejects_missing_ledgers_without_empty_state_confirmation(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(
        CaptureLedgerError,
        match="explicit confirmation of no historical T12 state",
    ):
        initialize_t12_ledgers(
            exposure_path=tmp_path / "t12-exposures.jsonl",
            capture_path=tmp_path / "t12-captures.jsonl",
            disposition_path=tmp_path / "t12-dispositions.jsonl",
            artifact_store=store,
            no_historical_t12_state=False,
        )


def test_initialize_t12_ledgers_rejects_nonempty_disposition_history_without_complete_boundary(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    disposition_path.touch()
    receipt = build_disposition_receipt(
        disposed_at=DISPOSED_AT,
        attempt_id=sample_attempt_id(tmp_path),
        disposition_code="refusal",
    )
    append_disposition_record(disposition_path, receipt)
    with pytest.raises(
        CaptureLedgerError,
        match="disposition history exists without the complete",
    ):
        initialize_t12_ledgers(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path,
            artifact_store=store,
            no_historical_t12_state=True,
        )


def test_initialize_t12_ledgers_rejects_shared_paths(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    shared = tmp_path / "t12-shared.jsonl"
    with pytest.raises(CaptureLedgerError, match="distinct paths"):
        initialize_t12_ledgers(
            exposure_path=shared,
            capture_path=tmp_path / "t12-captures.jsonl",
            disposition_path=shared,
            artifact_store=store,
            no_historical_t12_state=True,
        )


# --- record_refusal / record_explicit_skip / permit handling ---


def test_record_refusal_produces_a_disposition_receipt(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    receipt = record_disposition(runtime, permit, "refusal")
    assert isinstance(receipt, OperationalDispositionReceipt)
    assert receipt.attempt_id == runtime.attempt_id
    assert receipt.disposition_code == "refusal"
    assert read_disposition_ledger(runtime.disposition_path) == (receipt,)


def test_record_explicit_skip_produces_a_disposition_receipt(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    receipt = record_disposition(runtime, permit, "explicit_skip")
    assert isinstance(receipt, OperationalDispositionReceipt)
    assert receipt.disposition_code == "explicit_skip"
    assert read_disposition_ledger(runtime.disposition_path) == (receipt,)


def test_disposition_slot_cannot_be_recorded_twice_for_the_same_attempt(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    with pytest.raises(
        DispositionLedgerError, match="disposition slot already exists for attempt"
    ):
        record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )


def test_capture_then_disposition_is_rejected_by_mutual_exclusion(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    capture_response(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"a captured answer",
    )
    with pytest.raises(
        DispositionLedgerError,
        match="disposition cannot be recorded for an attempt with an existing capture",
    ):
        record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )


def test_disposition_then_capture_is_rejected_by_mutual_exclusion(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    with pytest.raises(
        CaptureLedgerError,
        match="capture cannot be recorded for an attempt with an existing disposition",
    ):
        capture_response(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            display_permit=permit,
            response_bytes=b"a captured answer",
        )


def test_disposition_cannot_be_recorded_for_s_channel(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "S")
    permit.consume()
    with pytest.raises(
        DispositionLedgerError, match="non-R/L/W exposure channel"
    ):
        record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )


def test_fabricated_display_permit_is_rejected_for_disposition(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")

    class _FakePermit:
        attempt_id = "fake"

    with pytest.raises(TypeError, match="exact issued DisplayPermit"):
        record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=_FakePermit(),  # type: ignore[arg-type]
            disposed_at=DISPOSED_AT,
        )


def test_permit_cannot_dispose_a_different_attempt(tmp_path: Path) -> None:
    first, first_permit = make_runtime(tmp_path / "first", "R")
    second, _second_permit = make_runtime(tmp_path / "second", "R")
    first_permit.consume()

    assert "attempt_id" not in inspect.signature(record_refusal).parameters
    with pytest.raises(DispositionLedgerError, match="exactly one compatible"):
        record_refusal(
            exposure_path=second.exposure_path,
            capture_path=second.capture_path,
            disposition_path=second.disposition_path,
            artifact_store=second.store,
            display_permit=first_permit,
            disposed_at=DISPOSED_AT,
        )
    assert read_disposition_ledger(first.disposition_path) == ()
    assert read_disposition_ledger(second.disposition_path) == ()


def test_unconsumed_display_permit_is_rejected_for_disposition(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    with pytest.raises(
        ExposureLedgerError, match="must be consumed before disposition recording"
    ):
        record_refusal(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
        )
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_display_permit_cannot_be_consumed_twice(tmp_path: Path) -> None:
    _runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    with pytest.raises(ExposureLedgerError, match="already been consumed"):
        permit.consume()


# --- close_text_submission: frozen D67 classification table ---


@pytest.mark.parametrize(
    ("raw_bytes", "expected_authority"),
    (
        (None, "no_response"),
        (b"", "no_response"),
        (b"   ", "no_response"),
        (b"\t\n", "no_response"),
        (b"\xc2\xa0", "no_response"),
        (b"\xe2\x80\x8b", "capture"),
        (b"\x00", "capture"),
        (b"\xff", "invalid_artifact"),
        (b"  exact submitted bytes stay unstripped  ", "capture"),
    ),
    ids=(
        "none",
        "empty-bytes",
        "spaces",
        "tab-newline",
        "nbsp",
        "zwsp",
        "nul",
        "invalid-utf8",
        "valid-non-whitespace",
    ),
)
def test_close_text_submission_frozen_classification_table(
    tmp_path: Path,
    raw_bytes: bytes | None,
    expected_authority: str,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    authority = close_text_submission(
        raw_bytes=raw_bytes,
        display_permit=permit,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        disposed_at=DISPOSED_AT,
    )

    if expected_authority == "capture":
        assert isinstance(authority, CaptureReceipt)
        assert raw_bytes is not None
        assert runtime.store.read(authority.response_artifact_ref) == raw_bytes
        assert read_capture_ledger(runtime.capture_path) == (authority,)
        assert read_disposition_ledger(runtime.disposition_path) == ()
    else:
        assert isinstance(authority, OperationalDispositionReceipt)
        assert authority.disposition_code == expected_authority
        assert read_disposition_ledger(runtime.disposition_path) == (authority,)
        assert read_capture_ledger(runtime.capture_path) == ()
        if expected_authority == "invalid_artifact":
            assert raw_bytes is not None
            expected_ref = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
            with pytest.raises(ArtifactStoreError):
                runtime.store.read(expected_ref)


def test_close_text_submission_rejects_non_bytes_raw_bytes(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    with pytest.raises(TypeError, match="raw_bytes must be exact bytes or None"):
        close_text_submission(
            raw_bytes="not bytes",  # type: ignore[arg-type]
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_disposition_ledger(runtime.disposition_path) == ()
    assert read_capture_ledger(runtime.capture_path) == ()


# --- production routing and infrastructure-failure authority invariants ---


@dataclass(frozen=True)
class _StaticCallSite:
    path: str
    scope: str
    line: int

    def render(self) -> str:
        return f"{self.path}:{self.scope}:{self.line}"


class _NamedCallVisitor(ast.NodeVisitor):
    """Small repository-specific alias-aware call-site scanner."""

    def __init__(self, target_name: str) -> None:
        self.target_name = target_name
        self.name_aliases = {target_name}
        self.scope: list[str] = []
        self.calls: list[tuple[str, int]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module.endswith("response_capture"):
            for alias in node.names:
                if alias.name == self.target_name:
                    self.name_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        direct_alias = (
            isinstance(node.func, ast.Name)
            and node.func.id in self.name_aliases
        )
        attribute_call = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == self.target_name
        )
        if direct_alias or attribute_call:
            self.calls.append((".".join(self.scope) or "<module>", node.lineno))
        self.generic_visit(node)

    def _visit_scoped(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        name: str,
    ) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()


def _production_sources() -> dict[str, str]:
    repository_root = Path(__file__).parents[1]
    vocab_root = repository_root / "vocab"
    return {
        path.relative_to(repository_root).as_posix(): path.read_text(encoding="utf-8")
        for path in vocab_root.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def _scan_named_calls(
    sources: dict[str, str],
    target_name: str,
) -> list[_StaticCallSite]:
    result: list[_StaticCallSite] = []
    for path, source in sorted(sources.items()):
        visitor = _NamedCallVisitor(target_name)
        visitor.visit(ast.parse(source, filename=path))
        result.extend(
            _StaticCallSite(path=path, scope=scope, line=line)
            for scope, line in visitor.calls
        )
    return result


def _assert_exact_callers(
    call_sites: list[_StaticCallSite],
    *,
    allowed_callers: frozenset[tuple[str, str]],
    invariant_name: str,
) -> None:
    actual_callers = [(site.path, site.scope) for site in call_sites]
    unexpected = [
        site
        for site in call_sites
        if (site.path, site.scope) not in allowed_callers
    ]
    missing = sorted(allowed_callers - set(actual_callers))
    duplicate_approved = sorted(
        caller for caller in allowed_callers if actual_callers.count(caller) != 1
    )
    if unexpected or missing or duplicate_approved:
        details = [f"unapproved {site.render()}" for site in unexpected]
        details.extend(f"missing {path}:{scope}" for path, scope in missing)
        details.extend(
            f"approved caller occurs other than once {path}:{scope}"
            for path, scope in duplicate_approved
        )
        raise AssertionError(f"{invariant_name}: " + "; ".join(details))


_APPROVED_RLW_TEXT_CAPTURE_CALLERS = frozenset(
    {("vocab/response_capture.py", "close_text_submission")}
)
# Static Python cannot infer an arbitrary caller's channel. A future legitimate
# S capture boundary must be named explicitly here; it is not globally banned
# by the R/L/W text-routing invariant.
_APPROVED_S_CAPTURE_CALLERS: frozenset[tuple[str, str]] = frozenset()


def test_rlw_capture_response_routing_is_recursively_alias_hardened() -> None:
    call_sites = _scan_named_calls(_production_sources(), "capture_response")
    _assert_exact_callers(
        call_sites,
        allowed_callers=(
            _APPROVED_RLW_TEXT_CAPTURE_CALLERS | _APPROVED_S_CAPTURE_CALLERS
        ),
        invariant_name="R/L/W capture_response routing invariant",
    )


def test_ast_routing_failure_names_module_alias_async_class_and_nested_calls() -> None:
    bypasses = {
        "vocab/nested/bypass.py": """
from ..response_capture import capture_response as _cap
import vocab.response_capture as rc

rc.capture_response()

class Bypass:
    capture_response()

    async def submit(self):
        _cap()
""",
    }
    call_sites = _scan_named_calls(bypasses, "capture_response")
    with pytest.raises(AssertionError) as raised:
        _assert_exact_callers(
            call_sites,
            allowed_callers=frozenset(),
            invariant_name="synthetic routing invariant",
        )
    message = str(raised.value)
    assert "vocab/nested/bypass.py:<module>:5" in message
    assert "vocab/nested/bypass.py:Bypass:8" in message
    assert "vocab/nested/bypass.py:Bypass.submit:11" in message


def test_infrastructure_failure_recorder_has_one_bounded_production_caller() -> None:
    recorder_name = "_record_capture_subsystem_infrastructure_failure"
    assert hasattr(response_capture_module, recorder_name)
    _assert_exact_callers(
        _scan_named_calls(_production_sources(), recorder_name),
        allowed_callers=frozenset(
            {("vocab/response_capture.py", "close_text_submission")}
        ),
        invariant_name="infrastructure_failure origin invariant",
    )


def test_infrastructure_ast_rejects_aliased_session_and_async_callers() -> None:
    bypasses = {
        "vocab/session_ui.py": """
from .response_capture import (
    _record_capture_subsystem_infrastructure_failure as record_failure,
)

async def finish_attempt():
    record_failure()
""",
        "vocab/nested/worker.py": """
import vocab.response_capture as rc

rc._record_capture_subsystem_infrastructure_failure()
""",
    }
    call_sites = _scan_named_calls(
        bypasses,
        "_record_capture_subsystem_infrastructure_failure",
    )
    with pytest.raises(AssertionError) as raised:
        _assert_exact_callers(
            call_sites,
            allowed_callers=frozenset(),
            invariant_name="synthetic infrastructure invariant",
        )
    message = str(raised.value)
    assert "vocab/session_ui.py:finish_attempt:7" in message
    assert "vocab/nested/worker.py:<module>:4" in message


def test_arbitrary_caller_cannot_select_infrastructure_failure(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    with pytest.raises(TypeError, match="bounded capture-commit path"):
        response_capture_module._append_disposition(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            display_permit=permit,
            disposed_at=DISPOSED_AT,
            disposition_code="infrastructure_failure",
        )
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_arbitrary_capture_exception_does_not_create_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def fail_artifact_put(_store: ArtifactStore, _value: bytes) -> str:
        raise RuntimeError("arbitrary storage exception")

    monkeypatch.setattr(ArtifactStore, "put", fail_artifact_put)
    with pytest.raises(RuntimeError, match="arbitrary storage exception"):
        close_text_submission(
            raw_bytes=b"capture-eligible response",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_bounded_capture_commit_failure_records_infrastructure_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    raw = b"verified response whose capture receipt cannot be committed"
    expected_ref = "sha256:" + hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        fail_capture_commit_before_write,
    )

    receipt = close_text_submission(
        raw_bytes=raw,
        display_permit=permit,
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        disposed_at=DISPOSED_AT,
    )

    assert isinstance(receipt, OperationalDispositionReceipt)
    assert receipt.disposition_code == "infrastructure_failure"
    assert runtime.store.read(expected_ref) == raw
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == (receipt,)
    assert resume_captured_response(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        attempt_id=runtime.attempt_id,
    ) is None

    evidence = runtime.disposition_evidence()
    assert evidence.disposition_code == "infrastructure_failure"
    assert not hasattr(evidence, "response_artifact_ref")
    assert not hasattr(evidence, "response_bytes")
    payload = plan_policy_judge(
        disposition=evidence,
        unit=validate_unit_evidence(make_unit("R")),
    ).to_payload()
    assert payload["reason_code"] == "infrastructure_failure"
    assert "response_artifact_ref" not in payload


def test_capture_commit_that_writes_receipt_then_raises_creates_no_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    real_append = response_capture_module._append_capture_record

    def append_then_raise(path: str | Path, record: CaptureReceipt) -> None:
        real_append(path, record)
        raise CaptureLedgerError("simulated capture readback failure")

    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        append_then_raise,
    )
    with pytest.raises(CaptureLedgerError, match="simulated capture readback"):
        close_text_submission(
            raw_bytes=b"receipt becomes durable before the reported failure",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert len(read_capture_ledger(runtime.capture_path)) == 1
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_partial_capture_commit_then_failure_creates_no_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def append_partial_then_raise(path: str | Path, _record: CaptureReceipt) -> None:
        capture_path = Path(path)
        capture_path.write_bytes(capture_path.read_bytes() + b'{"partial"')
        raise CaptureLedgerError("simulated partial capture append")

    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        append_partial_then_raise,
    )
    with pytest.raises(CaptureLedgerError, match="malformed final record"):
        close_text_submission(
            raw_bytes=b"response before partial capture-ledger corruption",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_unreadable_post_failure_history_creates_no_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    real_validate = response_capture_module.validate_t12_histories
    validation_count = 0

    def fail_second_validation(**kwargs: object):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            raise CaptureLedgerError("capture ledger is missing or unreadable")
        return real_validate(**kwargs)

    monkeypatch.setattr(
        response_capture_module,
        "validate_t12_histories",
        fail_second_validation,
    )
    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        fail_capture_commit_before_write,
    )
    with pytest.raises(CaptureLedgerError, match="missing or unreadable"):
        close_text_submission(
            raw_bytes=b"response before unreadable post-failure history",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert validation_count == 2
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_existing_disposition_after_capture_commit_failure_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def append_disposition_then_raise(
        _path: str | Path,
        record: CaptureReceipt,
    ) -> None:
        disposition = build_disposition_receipt(
            disposed_at=DISPOSED_AT,
            attempt_id=record.attempt_id,
            disposition_code="refusal",
        )
        append_disposition_record(runtime.disposition_path, disposition)
        raise CaptureLedgerError("simulated capture commit failure")

    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        append_disposition_then_raise,
    )
    with pytest.raises(CaptureLedgerError, match="simulated capture commit"):
        close_text_submission(
            raw_bytes=b"response racing with an existing disposition",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    dispositions = read_disposition_ledger(runtime.disposition_path)
    assert len(dispositions) == 1
    assert dispositions[0].disposition_code == "refusal"


def test_artifact_store_put_failure_creates_no_infrastructure_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def fail_put(_store: ArtifactStore, _data: bytes) -> str:
        raise ArtifactStoreError("artifact publication failed")

    monkeypatch.setattr(ArtifactStore, "put", fail_put)
    with pytest.raises(ArtifactStoreError, match="publication failed"):
        close_text_submission(
            raw_bytes=b"response whose artifact cannot be published",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_artifact_readback_failure_creates_no_infrastructure_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def fail_read(_store: ArtifactStore, _ref: object) -> bytes:
        raise ArtifactStoreError("artifact readback failed")

    monkeypatch.setattr(ArtifactStore, "read", fail_read)
    with pytest.raises(ArtifactStoreError, match="readback failed"):
        close_text_submission(
            raw_bytes=b"response whose artifact readback fails",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


@pytest.mark.parametrize(
    ("failure", "expected_type"),
    (
        (RuntimeError("arbitrary commit exception"), RuntimeError),
        (KeyboardInterrupt(), KeyboardInterrupt),
        (SystemExit("simulated process exit"), SystemExit),
    ),
    ids=("arbitrary-exception", "keyboard-interrupt", "process-exit"),
)
def test_non_capture_ledger_commit_failures_create_no_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_type: type[BaseException],
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def fail_commit(_path: str | Path, _record: CaptureReceipt) -> None:
        raise failure

    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        fail_commit,
    )
    with pytest.raises(expected_type):
        close_text_submission(
            raw_bytes=b"response interrupted outside bounded authority",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_capture_ledger_error_before_commit_creates_no_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()

    def fail_preflight(**_kwargs: object):
        raise CaptureLedgerError("arbitrary pre-commit capture error")

    monkeypatch.setattr(
        response_capture_module,
        "validate_t12_histories",
        fail_preflight,
    )
    with pytest.raises(CaptureLedgerError, match="pre-commit"):
        close_text_submission(
            raw_bytes=b"response blocked before capture commit",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


def test_infrastructure_disposition_append_failure_has_no_transient_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    append_attempts = 0

    def fail_disposition_append(
        _path: str | Path,
        _record: OperationalDispositionReceipt,
    ) -> None:
        nonlocal append_attempts
        append_attempts += 1
        raise DispositionLedgerError("disposition ledger append failed")

    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        fail_capture_commit_before_write,
    )
    monkeypatch.setattr(
        response_capture_module,
        "_append_disposition_record",
        fail_disposition_append,
    )
    with pytest.raises(DispositionLedgerError, match="append failed"):
        close_text_submission(
            raw_bytes=b"response before disposition append failure",
            display_permit=permit,
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            disposed_at=DISPOSED_AT,
        )
    assert append_attempts == 1
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()
    with pytest.raises(
        AssessmentEvidenceError,
        match="requires exactly one disposition receipt",
    ):
        runtime.disposition_evidence()


def test_bounded_capture_commit_failure_on_s_creates_no_d67_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, permit = make_runtime(tmp_path, "S")
    permit.consume()
    monkeypatch.setattr(
        response_capture_module,
        "_append_capture_record",
        fail_capture_commit_before_write,
    )
    with pytest.raises(CaptureLedgerError, match="capture ledger append failed"):
        capture_response(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            captured_at=CAPTURED_AT,
            display_permit=permit,
            response_bytes=b"spoken response bytes",
        )
    assert read_capture_ledger(runtime.capture_path) == ()
    assert read_disposition_ledger(runtime.disposition_path) == ()


# --- crash / no-receipt: absence is not a disposition ---


def test_reservation_without_capture_or_disposition_leaves_no_disposition_record(
    tmp_path: Path,
) -> None:
    runtime, _permit = make_runtime(tmp_path, "R")
    assert read_disposition_ledger(runtime.disposition_path) == ()
    with pytest.raises(
        AssessmentEvidenceError, match="requires exactly one disposition receipt"
    ):
        runtime.disposition_evidence()


# --- ValidatedDispositionEvidence reconstruction, seal, and mutation checks ---


def test_load_validated_disposition_evidence_reconstructs_after_refusal(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    evidence = runtime.disposition_evidence()
    assert evidence.attempt_id == runtime.attempt_id
    assert evidence.session_id == runtime.manifest.session_id
    assert evidence.unit_key == UNIT_KEY
    assert evidence.channel == "R"
    assert evidence.disposition_code == "refusal"


def test_load_validated_disposition_evidence_requires_a_disposition_not_a_capture(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    permit.consume()
    capture_response(
        exposure_path=runtime.exposure_path,
        capture_path=runtime.capture_path,
        disposition_path=runtime.disposition_path,
        artifact_store=runtime.store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"a captured answer",
    )
    with pytest.raises(
        AssessmentEvidenceError, match="requires exactly one disposition receipt"
    ):
        runtime.disposition_evidence()


def test_resume_captured_response_rejects_durable_capture_disposition_coexistence(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    create_durable_capture_disposition_coexistence(runtime, permit)

    with pytest.raises(DispositionLedgerError, match="mutual exclusion violated"):
        resume_captured_response(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            attempt_id=runtime.attempt_id,
        )


def test_load_validated_attempt_evidence_rejects_durable_coexistence(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    create_durable_capture_disposition_coexistence(runtime, permit)

    with pytest.raises(DispositionLedgerError, match="mutual exclusion violated"):
        load_validated_attempt_evidence(
            exposure_path=runtime.exposure_path,
            capture_path=runtime.capture_path,
            disposition_path=runtime.disposition_path,
            artifact_store=runtime.store,
            session_root=runtime.session_root,
            attempt_id=runtime.attempt_id,
        )


def test_load_validated_disposition_evidence_rejects_durable_coexistence(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    create_durable_capture_disposition_coexistence(runtime, permit)

    with pytest.raises(DispositionLedgerError, match="mutual exclusion violated"):
        runtime.disposition_evidence()


def test_plan_policy_judge_rejects_a_fabricated_disposition_evidence_instance() -> None:
    fake = object.__new__(ValidatedDispositionEvidence)
    unit = validate_unit_evidence(make_unit("R"))
    with pytest.raises(
        TypeError, match="was not issued by load_validated_disposition_evidence"
    ):
        plan_policy_judge(disposition=fake, unit=unit)


def test_plan_policy_judge_rejects_a_mutated_disposition_evidence(
    tmp_path: Path,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    evidence = runtime.disposition_evidence()
    unit = validate_unit_evidence(make_unit("R"))
    object.__setattr__(evidence, "disposition_code", "explicit_skip")
    with pytest.raises(AssessmentEvidenceError, match="issuance snapshot"):
        plan_policy_judge(disposition=evidence, unit=unit)


# --- plan_policy_judge: all five closed codes, every R/L/W channel ---


@pytest.mark.parametrize("code", sorted(DISPOSITION_CODES))
def test_plan_policy_judge_covers_every_disposition_code(
    tmp_path: Path,
    code: str,
) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, code)
    evidence = runtime.disposition_evidence()
    unit = validate_unit_evidence(make_unit("R"))
    judge = plan_policy_judge(disposition=evidence, unit=unit)
    assert isinstance(judge, PlannedJudge)
    payload = judge.to_payload()
    assert payload["outcome"] == ASSESSMENT_OUTCOME_ABSTAIN
    assert payload["passed"] is False
    assert payload["authority_kind"] == "policy"
    assert payload["model_id"] == planning_module.POLICY_ID
    assert payload["model_version"] == str(planning_module.POLICY_VERSION)
    assert payload["reason_code"] == code
    assert payload["attempt_id"] == runtime.attempt_id
    assert payload["presented_stimulus_ref"] == runtime.item["presented_stimulus_ref"]
    assert set(payload["provenance"]) == {"policy"}
    assert payload["provenance"]["policy"] == {
        "policy_id": planning_module.POLICY_ID,
        "policy_version": planning_module.POLICY_VERSION,
    }
    assert set(payload).isdisjoint(
        {
            "assessment_id",
            "stimulus_ref",
            "novel",
            "failure_code",
            "response_artifact_ref",
        }
    )


@pytest.mark.parametrize("channel", ["R", "L", "W"])
def test_plan_policy_judge_supports_every_rlw_channel(
    tmp_path: Path,
    channel: str,
) -> None:
    runtime, permit = make_runtime(tmp_path, channel)
    record_disposition(runtime, permit, "refusal")
    evidence = runtime.disposition_evidence()
    unit = validate_unit_evidence(make_unit(channel))
    judge = plan_policy_judge(disposition=evidence, unit=unit)
    payload = judge.to_payload()
    assert payload["channel"] == channel
    assert payload["reason_code"] == "refusal"


def test_plan_policy_judge_rejects_unit_key_mismatch(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    evidence = runtime.disposition_evidence()
    other_unit = validate_unit_evidence(make_other_unit("R"))
    with pytest.raises(AssessmentEvidenceError, match="unit_key do not match"):
        plan_policy_judge(disposition=evidence, unit=other_unit)


def test_plan_policy_judge_rejects_disabled_channel(tmp_path: Path) -> None:
    runtime, permit = make_runtime(tmp_path, "R")
    record_disposition(runtime, permit, "refusal")
    evidence = runtime.disposition_evidence()
    unit_without_r = validate_unit_evidence(make_unit("R", enabled_channel="L"))
    with pytest.raises(AssessmentEvidenceError, match="channel is not enabled"):
        plan_policy_judge(disposition=evidence, unit=unit_without_r)


# --- static routing invariant: disposition and semantic ABSTAIN reasons never collide ---


def test_semantic_abstain_reason_codes_and_disposition_codes_are_disjoint() -> None:
    assert set(SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES).isdisjoint(DISPOSITION_CODES)
    assert "reviewer_rejected" not in DISPOSITION_CODES
