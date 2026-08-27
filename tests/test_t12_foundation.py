"""T12.1 identity, durable exposure, and response-capture invariants."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

import vocab.exposure as exposure_module
import vocab.response_capture as response_capture_module
import vocab.session as session_module
from vocab.artifact_json import canonical_json_bytes
from vocab.artifact_store import ArtifactStore, ArtifactStoreError
from vocab.assessment_identity import (
    ATTEMPT_DOMAIN,
    COGNITIVE_STIMULUS_DOMAIN,
    assessment_attempt_id,
    cognitive_stimulus_projection,
    cognitive_stimulus_ref,
)
from vocab.exposure import (
    DisplayPermit,
    ExposureLedgerError,
    novelty_for_reserved_attempt,
    read_exposure_ledger,
    reserve_exposure,
)
from vocab.response_capture import (
    CaptureLedgerError,
    capture_response,
    initialize_t12_ledgers,
    read_capture_ledger,
    resume_captured_response,
)
from vocab.session import (
    SESSION_MANIFEST_ARTIFACT,
    SessionManifestError,
    create_session_manifest,
    import_session_manifest,
    load_session_manifest,
    persist_session_manifest,
    serialize_session_manifest,
)


UNIT_KEY = "subtle::small-difference"
CREATED_AT = "2026-08-25T01:00:00+00:00"
RESERVED_AT = "2026-08-25T01:01:00+00:00"
CAPTURED_AT = "2026-08-25T01:02:00+00:00"


def l_stimulus(script: str = "The difference was subtle.") -> dict[str, str]:
    return {
        "spoken_script": script,
        "question": "How was the difference described?",
    }


def make_item(
    store: ArtifactStore,
    *,
    ordinal: int = 0,
    stimulus: dict[str, str] | None = None,
) -> dict[str, object]:
    stimulus = stimulus or l_stimulus()
    stimulus_ref = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=stimulus,
    )
    artifact_ref = store.put(canonical_json_bytes(stimulus))
    return {
        "item_ordinal": ordinal,
        "unit_key": UNIT_KEY,
        "channel": "L",
        "task_kind": "listening_comprehension",
        "stimulus": stimulus,
        "presented_stimulus_ref": stimulus_ref,
        "stimulus_artifact_ref": artifact_ref,
    }


def make_manifest_and_attempt(
    store: ArtifactStore,
) -> tuple[session_module.SessionManifest, dict[str, object], str]:
    item = make_item(store)
    manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    attempt_id = assessment_attempt_id(
        session_id=manifest.session_id,
        item_ordinal=item["item_ordinal"],
        unit_key=item["unit_key"],
        channel=item["channel"],
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    return manifest, item, attempt_id


def disposition_path_for(exposure_path: Path) -> Path:
    return exposure_path.parent / "t12-dispositions.jsonl"


def initialized_runtime(
    tmp_path: Path,
) -> tuple[ArtifactStore, Path, Path, session_module.SessionManifest, dict[str, object], str]:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=True,
    )
    manifest, item, attempt_id = make_manifest_and_attempt(store)
    persist_session_manifest(tmp_path / "sessions", manifest)
    return store, exposure_path, capture_path, manifest, item, attempt_id


def reserve(
    store: ArtifactStore,
    exposure_path: Path,
    capture_path: Path,
    manifest: session_module.SessionManifest,
    item: dict[str, object],
    attempt_id: str,
    *,
    reserved_at: str = RESERVED_AT,
) -> DisplayPermit:
    permit = reserve_exposure(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        session_root=exposure_path.parent / "sessions",
        session_id=manifest.session_id,
        item_ordinal=item["item_ordinal"],
        reserved_at=reserved_at,
    )
    assert permit.attempt_id == attempt_id
    return permit


def test_l_voice_and_rendered_artifact_do_not_change_cognitive_identity() -> None:
    stimulus = l_stimulus()
    first_voice = "voice-a"
    second_voice = "voice-b"
    first_artifact = "sha256:" + "1" * 64
    second_artifact = "sha256:" + "2" * 64

    first = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=stimulus,
    )
    second = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=dict(stimulus),
    )

    assert (first_voice, first_artifact) != (second_voice, second_artifact)
    assert first == second


def test_d54_formatting_normalization_preserves_identity() -> None:
    first = l_stimulus("  The\r\ndifference\twas subtle.  ")
    second = l_stimulus("The difference was subtle.")
    first["question"] = "How\u00a0was the difference described?"
    assert cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=first,
    ) == cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=second,
    )


@pytest.mark.parametrize(
    "changed",
    ("The difference was obvious.", "the difference was subtle.", "The difference was subtle!"),
)
def test_real_cognitive_case_or_punctuation_difference_changes_identity(
    changed: str,
) -> None:
    original = cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=l_stimulus(),
    )
    assert cognitive_stimulus_ref(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=l_stimulus(changed),
    ) != original


def test_cognitive_projection_has_exact_channel_shape() -> None:
    projection = cognitive_stimulus_projection(
        unit_key=UNIT_KEY,
        channel="L",
        task_kind="listening_comprehension",
        stimulus=l_stimulus(),
    )
    assert projection == {
        "domain": COGNITIVE_STIMULUS_DOMAIN,
        "v": 1,
        "unit_key": UNIT_KEY,
        "channel": "L",
        "task_kind": "listening_comprehension",
        "canonical_spoken_script": "The difference was subtle.",
        "canonical_question": "How was the difference described?",
    }


def test_same_persisted_manifest_loads_to_same_session_id(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    manifest, _, _ = make_manifest_and_attempt(store)
    stored_path = persist_session_manifest(tmp_path / "sessions", manifest)

    first = load_session_manifest(tmp_path / "sessions", manifest.session_id)
    second = load_session_manifest(tmp_path / "sessions", manifest.session_id)

    assert first.session_id == second.session_id == manifest.session_id
    assert stored_path.name == manifest.session_id.removeprefix("session:v1:")
    assert ":" not in stored_path.name


def test_identical_session_items_get_fresh_nonce_and_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    item = make_item(store)
    nonces = iter(("1" * 64, "2" * 64))
    monkeypatch.setattr(session_module.secrets, "token_hex", lambda size: next(nonces))

    first = create_session_manifest(created_at=CREATED_AT, items=[item])
    second = create_session_manifest(created_at=CREATED_AT, items=[item])

    assert first.to_dict()["session_nonce"] != second.to_dict()["session_nonce"]
    assert first.session_id != second.session_id


def test_attempt_identity_is_stable_within_session_and_changes_for_new_session(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    first_manifest, item, first_attempt = make_manifest_and_attempt(store)
    repeated = assessment_attempt_id(
        session_id=first_manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel="L",
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    second_manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    second_attempt = assessment_attempt_id(
        session_id=second_manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel="L",
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    assert first_attempt == repeated
    assert second_attempt != first_attempt
    assert ATTEMPT_DOMAIN == "vocab.t12.attempt"


def test_manifest_preserves_verbatim_stimulus_and_has_closed_shape(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    stimulus = l_stimulus("  Keep\nthis spacing.  ")
    item = make_item(store, ordinal=4, stimulus=stimulus)
    manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    data = manifest.to_dict()
    assert set(data) == {
        "artifact",
        "v",
        "session_nonce",
        "created_at",
        "producer",
        "producer_version",
        "items",
    }
    assert data["artifact"] == SESSION_MANIFEST_ARTIFACT
    assert data["items"][0]["stimulus"]["spoken_script"] == "  Keep\nthis spacing.  "
    assert serialize_session_manifest(manifest) == canonical_json_bytes(data)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_key",
        "unknown_key",
        "wrong_version_type",
        "mismatched_stimulus_ref",
        "invalid_artifact_ref",
        "duplicate_ordinal",
        "unsorted_ordinal",
    ),
)
def test_manifest_import_fails_closed_for_invalid_shape_or_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    manifest, _, _ = make_manifest_and_attempt(store)
    data = manifest.to_dict()
    if mutation == "missing_key":
        del data["producer"]
    elif mutation == "unknown_key":
        data["unknown"] = True
    elif mutation == "wrong_version_type":
        data["v"] = True
    elif mutation == "mismatched_stimulus_ref":
        data["items"][0]["presented_stimulus_ref"] = "stimulus:v1:" + "0" * 64
    elif mutation == "invalid_artifact_ref":
        data["items"][0]["stimulus_artifact_ref"] = "sha256:BAD"
    elif mutation == "duplicate_ordinal":
        data["items"].append(dict(data["items"][0]))
    elif mutation == "unsorted_ordinal":
        second = dict(data["items"][0])
        data["items"][0]["item_ordinal"] = 2
        second["item_ordinal"] = 1
        data["items"].append(second)
    with pytest.raises(SessionManifestError):
        import_session_manifest(canonical_json_bytes(data))


def test_manifest_persistence_rejects_conflicting_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    manifest, _, _ = make_manifest_and_attempt(store)
    path = persist_session_manifest(tmp_path / "sessions", manifest)
    path.write_bytes(b"conflict")
    with pytest.raises(SessionManifestError):
        persist_session_manifest(tmp_path / "sessions", manifest)


def test_artifact_store_content_identity_and_exact_readback(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    first = store.put(b"same bytes")
    repeated = store.put(b"same bytes")
    different = store.put(b"different bytes")
    assert first == repeated
    assert different != first
    assert store.read(first) == b"same bytes"


def test_artifact_store_corruption_fails_closed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put(b"trusted bytes")
    (store.root / ref.removeprefix("sha256:")).write_bytes(b"corrupt")
    with pytest.raises(ArtifactStoreError):
        store.read(ref)
    with pytest.raises(ArtifactStoreError):
        store.put(b"trusted bytes")


def test_orphan_artifact_has_no_attempt_binding(tmp_path: Path) -> None:
    store, exposure_path, capture_path, _, _, attempt_id = initialized_runtime(tmp_path)
    orphan_ref = store.put(b"unbound response")
    assert store.read(orphan_ref) == b"unbound response"
    assert resume_captured_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        attempt_id=attempt_id,
    ) is None


def test_initialization_requires_explicit_empty_history_authority(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(CaptureLedgerError):
        initialize_t12_ledgers(
            exposure_path=tmp_path / "t12-exposures.jsonl",
            capture_path=tmp_path / "t12-captures.jsonl",
            disposition_path=tmp_path / "t12-dispositions.jsonl",
            artifact_store=store,
            no_historical_t12_state=False,
        )


def test_initialization_creates_and_validates_both_empty_ledgers(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    exposure_path = tmp_path / "t12-exposures.jsonl"
    capture_path = tmp_path / "t12-captures.jsonl"
    disposition_path = tmp_path / "t12-dispositions.jsonl"
    assert initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=True,
    ) == ((), (), ())
    assert (
        exposure_path.read_bytes()
        == capture_path.read_bytes()
        == disposition_path.read_bytes()
        == b""
    )
    assert initialize_t12_ledgers(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path,
        artifact_store=store,
        no_historical_t12_state=False,
    ) == ((), (), ())


def test_self_consistent_fake_session_identity_cannot_authorize_reservation(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, _, item, _ = initialized_runtime(tmp_path)
    fake_session_id = "session:v1:" + "a" * 64
    fake_attempt_id = assessment_attempt_id(
        session_id=fake_session_id,
        item_ordinal=item["item_ordinal"],
        unit_key=item["unit_key"],
        channel=item["channel"],
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    assert fake_attempt_id.startswith("attempt:v1:")
    with pytest.raises(SessionManifestError, match="missing or unreadable"):
        reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            session_root=tmp_path / "sessions",
            session_id=fake_session_id,
            item_ordinal=item["item_ordinal"],
            reserved_at=RESERVED_AT,
        )
    assert read_exposure_ledger(exposure_path) == ()


def test_unpersisted_session_manifest_cannot_authorize_reservation(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, _, item, _ = initialized_runtime(tmp_path)
    unpersisted = create_session_manifest(created_at=CREATED_AT, items=[item])
    with pytest.raises(SessionManifestError, match="missing or unreadable"):
        reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            session_root=tmp_path / "sessions",
            session_id=unpersisted.session_id,
            item_ordinal=item["item_ordinal"],
            reserved_at=RESERVED_AT,
        )
    assert read_exposure_ledger(exposure_path) == ()


def test_persisted_manifest_item_is_exact_reservation_authority(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    reservation = read_exposure_ledger(exposure_path)[0]
    assert permit.attempt_id == attempt_id
    assert reservation.session_id == manifest.session_id
    assert reservation.item_ordinal == item["item_ordinal"]
    assert reservation.unit_key == item["unit_key"]
    assert reservation.channel == item["channel"]
    assert reservation.presented_stimulus_ref == item["presented_stimulus_ref"]
    assert reservation.stimulus_artifact_ref == item["stimulus_artifact_ref"]
    assert reservation.attempt_id == attempt_id


def test_persisted_manifest_missing_ordinal_fails_before_append(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, _, _ = initialized_runtime(tmp_path)
    with pytest.raises(ExposureLedgerError, match="item_ordinal"):
        reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            session_root=tmp_path / "sessions",
            session_id=manifest.session_id,
            item_ordinal=999,
            reserved_at=RESERVED_AT,
        )
    assert read_exposure_ledger(exposure_path) == ()


def test_tampered_persisted_manifest_fails_before_exposure_append(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, _ = initialized_runtime(tmp_path)
    manifest_path = (
        tmp_path
        / "sessions"
        / manifest.session_id.removeprefix("session:v1:")
    )
    manifest_path.write_bytes(b"{}")
    with pytest.raises(SessionManifestError):
        reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            session_root=tmp_path / "sessions",
            session_id=manifest.session_id,
            item_ordinal=item["item_ordinal"],
            reserved_at=RESERVED_AT,
        )
    assert read_exposure_ledger(exposure_path) == ()


@pytest.mark.parametrize("corruption", ("missing", "changed"))
def test_manifest_stimulus_artifact_must_verify_before_reservation(
    tmp_path: Path,
    corruption: str,
) -> None:
    store, exposure_path, capture_path, manifest, item, _ = initialized_runtime(tmp_path)
    artifact_path = store.root / item["stimulus_artifact_ref"].removeprefix("sha256:")
    if corruption == "missing":
        artifact_path.unlink()
    else:
        artifact_path.write_bytes(b"corrupt stimulus")
    with pytest.raises(ArtifactStoreError):
        reserve_exposure(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            session_root=tmp_path / "sessions",
            session_id=manifest.session_id,
            item_ordinal=item["item_ordinal"],
            reserved_at=RESERVED_AT,
        )
    assert read_exposure_ledger(exposure_path) == ()


def test_novelty_rejects_attempt_absent_from_physical_history(tmp_path: Path) -> None:
    _, exposure_path, _, _, _, attempt_id = initialized_runtime(tmp_path)
    with pytest.raises(ExposureLedgerError, match="durable current reservation"):
        novelty_for_reserved_attempt(exposure_path, attempt_id)


def test_reservation_is_durable_before_permit_and_permit_is_one_use(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    assert read_exposure_ledger(exposure_path)[0].attempt_id == permit.attempt_id
    assert permit.novel is True
    assert permit.consumed is False
    permit.consume()
    assert permit.consumed is True
    with pytest.raises(ExposureLedgerError):
        permit.consume()


def test_failed_reservation_never_issues_permit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise ExposureLedgerError("simulated append failure")

    monkeypatch.setattr(exposure_module, "_append_exposure_record", fail_append)
    with pytest.raises(ExposureLedgerError, match="simulated"):
        reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    assert read_exposure_ledger(exposure_path) == ()


def test_restart_cannot_recreate_display_permit_from_reservation(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    persisted = read_exposure_ledger(exposure_path)[0]
    with pytest.raises(TypeError):
        DisplayPermit(persisted.attempt_id, True)
    with pytest.raises(TypeError):
        capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            captured_at=CAPTURED_AT,
            response_bytes=b"restart cannot freshly capture",
        )
    assert read_capture_ledger(capture_path) == ()


@pytest.mark.parametrize("prior_final_style", ("OMITTED", "ABSTAIN", "interrupted"))
def test_any_prior_different_reserved_attempt_consumes_novelty(
    tmp_path: Path,
    prior_final_style: str,
) -> None:
    store, exposure_path, capture_path, first_manifest, item, first_attempt = (
        initialized_runtime(tmp_path)
    )
    first_permit = reserve(
        store,
        exposure_path,
        capture_path,
        first_manifest,
        item,
        first_attempt,
    )
    # The exposure ledger deliberately contains no outcome. OMITTED, ABSTAIN,
    # and interruption therefore have identical D55 novelty authority.
    if prior_final_style != "interrupted":
        first_permit.consume()
    second_manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    persist_session_manifest(exposure_path.parent / "sessions", second_manifest)
    second_attempt = assessment_attempt_id(
        session_id=second_manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel="L",
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    second_permit = reserve(
        store,
        exposure_path,
        capture_path,
        second_manifest,
        item,
        second_attempt,
    )
    assert first_permit.novel is True
    assert second_permit.novel is False


def test_physical_order_not_reserved_at_defines_novelty(tmp_path: Path) -> None:
    store, exposure_path, capture_path, first_manifest, item, first_attempt = (
        initialized_runtime(tmp_path)
    )
    first_permit = reserve(
        store,
        exposure_path,
        capture_path,
        first_manifest,
        item,
        first_attempt,
        reserved_at="2026-08-25T03:00:00+00:00",
    )
    second_manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    persist_session_manifest(exposure_path.parent / "sessions", second_manifest)
    second_attempt = assessment_attempt_id(
        session_id=second_manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel="L",
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    second_permit = reserve(
        store,
        exposure_path,
        capture_path,
        second_manifest,
        item,
        second_attempt,
        reserved_at="2026-08-25T00:00:00+00:00",
    )
    assert first_permit.novel is True
    assert second_permit.novel is False
    assert novelty_for_reserved_attempt(exposure_path, first_attempt) is True
    assert novelty_for_reserved_attempt(exposure_path, second_attempt) is False


def test_post_append_novelty_failure_issues_no_permit_but_keeps_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )

    def fail_novelty(*_args: object, **_kwargs: object) -> bool:
        raise ExposureLedgerError("simulated post-append novelty failure")

    monkeypatch.setattr(
        exposure_module,
        "novelty_for_reserved_attempt",
        fail_novelty,
    )
    with pytest.raises(ExposureLedgerError, match="post-append"):
        reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    history = read_exposure_ledger(exposure_path)
    assert len(history) == 1
    assert history[0].attempt_id == attempt_id


def test_duplicate_reservation_slot_fails_even_if_identical(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    with pytest.raises(ExposureLedgerError, match="already reserved"):
        reserve(store, exposure_path, capture_path, manifest, item, attempt_id)


def test_malformed_final_exposure_record_fails_closed(tmp_path: Path) -> None:
    store, exposure_path, _, _, _, _ = initialized_runtime(tmp_path)
    assert isinstance(store, ArtifactStore)
    with exposure_path.open("ab") as handle:
        handle.write(b'{"v":')
    with pytest.raises(ExposureLedgerError, match="malformed final"):
        read_exposure_ledger(exposure_path)


def test_two_attempts_with_same_response_share_bytes_but_not_capture_slot(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, first_manifest, item, first_attempt = (
        initialized_runtime(tmp_path)
    )
    first_permit = reserve(
        store, exposure_path, capture_path, first_manifest, item, first_attempt
    )
    second_manifest = create_session_manifest(created_at=CREATED_AT, items=[item])
    persist_session_manifest(exposure_path.parent / "sessions", second_manifest)
    second_attempt = assessment_attempt_id(
        session_id=second_manifest.session_id,
        item_ordinal=0,
        unit_key=UNIT_KEY,
        channel="L",
        presented_stimulus_ref=item["presented_stimulus_ref"],
    )
    second_permit = reserve(
        store, exposure_path, capture_path, second_manifest, item, second_attempt
    )
    assert "attempt_id" not in inspect.signature(capture_response).parameters
    with pytest.raises(AttributeError):
        first_permit._attempt_id = second_attempt
    first_permit.consume()
    second_permit.consume()
    first_receipt = capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=first_permit,
        response_bytes=b"same learner response",
    )
    second_receipt = capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=second_permit,
        response_bytes=b"same learner response",
    )
    assert first_receipt.response_artifact_ref == second_receipt.response_artifact_ref
    assert first_receipt.attempt_id == first_attempt
    assert second_receipt.attempt_id == second_attempt
    assert first_receipt.attempt_id != second_receipt.attempt_id
    assert len(read_capture_ledger(capture_path)) == 2


def test_crash_after_artifact_before_receipt_leaves_inert_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise CaptureLedgerError("simulated crash before receipt")

    monkeypatch.setattr(response_capture_module, "_append_capture_record", fail_append)
    with pytest.raises(CaptureLedgerError, match="simulated crash"):
        capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            captured_at=CAPTURED_AT,
            display_permit=permit,
            response_bytes=b"orphaned exact response",
        )
    orphan_ref = "sha256:" + hashlib.sha256(b"orphaned exact response").hexdigest()
    assert store.read(orphan_ref) == b"orphaned exact response"
    assert read_capture_ledger(capture_path) == ()
    assert resume_captured_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        attempt_id=attempt_id,
    ) is None


def test_valid_capture_is_resumable_without_creating_redisplay_authority(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    receipt = capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"captured response",
    )
    resumed = resume_captured_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        attempt_id=attempt_id,
    )
    assert resumed is not None
    assert resumed.receipt == receipt
    assert resumed.response_bytes == b"captured response"
    with pytest.raises(TypeError):
        DisplayPermit(attempt_id, False)


def test_artifact_without_capture_receipt_is_not_captured(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    store.put(b"response present without receipt")
    assert resume_captured_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        attempt_id=attempt_id,
    ) is None


def test_unconsumed_permit_fails_before_artifact_or_receipt_write(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    with pytest.raises(ExposureLedgerError, match="must be consumed"):
        capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            captured_at=CAPTURED_AT,
            display_permit=permit,
            response_bytes=b"must remain absent",
        )
    assert read_capture_ledger(capture_path) == ()
    ref = "sha256:" + hashlib.sha256(b"must remain absent").hexdigest()
    with pytest.raises(ArtifactStoreError):
        store.read(ref)


def test_fabricated_display_capability_is_rejected(tmp_path: Path) -> None:
    store, exposure_path, capture_path, _, _, _ = initialized_runtime(tmp_path)
    with pytest.raises(TypeError):
        DisplayPermit("attempt:v1:" + "0" * 64, True)
    fabricated = object.__new__(DisplayPermit)
    object.__setattr__(fabricated, "_attempt_id", "attempt:v1:" + "0" * 64)
    object.__setattr__(fabricated, "_consumed", True)
    object.__setattr__(fabricated, "_novel", True)
    with pytest.raises(TypeError, match="not issued"):
        capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            captured_at=CAPTURED_AT,
            display_permit=fabricated,
            response_bytes=b"fabricated capability response",
        )
    assert read_capture_ledger(capture_path) == ()
    ref = "sha256:" + hashlib.sha256(b"fabricated capability response").hexdigest()
    with pytest.raises(ArtifactStoreError):
        store.read(ref)


def test_second_capture_for_same_attempt_fails_closed(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"first",
    )
    with pytest.raises(CaptureLedgerError, match="already exists"):
        capture_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            captured_at=CAPTURED_AT,
            display_permit=permit,
            response_bytes=b"second",
        )


@pytest.mark.parametrize("corruption", ("missing", "changed"))
def test_capture_receipt_with_missing_or_corrupt_artifact_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    receipt = capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"trusted captured bytes",
    )
    artifact_path = store.root / receipt.response_artifact_ref.removeprefix("sha256:")
    if corruption == "missing":
        artifact_path.unlink()
    else:
        artifact_path.write_bytes(b"changed")
    with pytest.raises(CaptureLedgerError, match="missing or corrupt"):
        resume_captured_response(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            attempt_id=attempt_id,
        )


def test_nonempty_exposure_with_missing_capture_ledger_fails_closed(
    tmp_path: Path,
) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    capture_path.unlink()
    with pytest.raises(CaptureLedgerError, match="non-empty exposure"):
        initialize_t12_ledgers(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            no_historical_t12_state=True,
        )


def test_capture_history_with_missing_exposure_ledger_fails_closed(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"captured",
    )
    exposure_path.unlink()
    with pytest.raises(CaptureLedgerError, match="capture history"):
        initialize_t12_ledgers(
            exposure_path=exposure_path,
            capture_path=capture_path,
            disposition_path=disposition_path_for(exposure_path),
            artifact_store=store,
            no_historical_t12_state=True,
        )


def test_duplicate_physical_capture_slot_fails_even_if_identical(tmp_path: Path) -> None:
    store, exposure_path, capture_path, manifest, item, attempt_id = initialized_runtime(
        tmp_path
    )
    permit = reserve(store, exposure_path, capture_path, manifest, item, attempt_id)
    permit.consume()
    capture_response(
        exposure_path=exposure_path,
        capture_path=capture_path,
        disposition_path=disposition_path_for(exposure_path),
        artifact_store=store,
        captured_at=CAPTURED_AT,
        display_permit=permit,
        response_bytes=b"captured",
    )
    physical_line = capture_path.read_bytes()
    with capture_path.open("ab") as handle:
        handle.write(physical_line)
    with pytest.raises(CaptureLedgerError, match="duplicate physical"):
        read_capture_ledger(capture_path)


def test_t11_modules_do_not_import_t12_foundation_concerns() -> None:
    root = Path(__file__).parents[1] / "vocab"
    for name in (
        "semantic_request.py",
        "semantic_response.py",
        "human_review.py",
        "review_materialization.py",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert "assessment_identity" not in source
        assert "artifact_store" not in source
        assert "from .session" not in source
        assert "from .exposure" not in source
        assert "response_capture" not in source


def test_t12_foundation_has_no_anki_reconcile_t9_or_eventlog_dependency() -> None:
    root = Path(__file__).parents[1] / "vocab"
    forbidden_roots = {"anki", "reconcile", "events"}
    for name in (
        "assessment_identity.py",
        "artifact_store.py",
        "capture_ledger.py",
        "session.py",
        "exposure.py",
        "response_capture.py",
        "disposition_ledger.py",
        "t12_jsonl.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden_roots)


def test_exposure_and_capture_have_no_circular_private_helper_dependency() -> None:
    root = Path(__file__).parents[1] / "vocab"
    exposure_source = (root / "exposure.py").read_text(encoding="utf-8")
    response_source = (root / "response_capture.py").read_text(encoding="utf-8")
    response_tree = ast.parse(response_source)

    assert "response_capture" not in exposure_source
    exposure_imports = [
        alias.name
        for node in ast.walk(response_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "exposure"
        for alias in node.names
    ]
    assert exposure_imports
    assert all(not name.startswith("_") for name in exposure_imports)
    assert "_read_strict_canonical_jsonl" not in response_source
    assert "_append_strict_canonical_record" not in response_source

    jsonl_source = (root / "t12_jsonl.py").read_text(encoding="utf-8")
    assert "ExposureReservation" not in jsonl_source
    assert "CaptureReceipt" not in jsonl_source
