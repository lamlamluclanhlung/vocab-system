"""Wave C v1 operational tests for R/W assessment orchestration under D71.

Every refusal test asserts the absence of the forbidden side effect as well as
the refusal, and every ordering test asserts a recorded event sequence rather
than co-occurrence.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from vocab import cli
from vocab.artifact_json import strict_json_loads
from vocab.artifact_store import ArtifactStore
from vocab.contracts import NOTE_FIELDS
from vocab.runtime import assessment_session, attempt_runner, semantic_bridge
from vocab.runtime.artifact_store_gate import open_deployment_artifact_store
from vocab.runtime.errors import (
    RuntimeAssessmentError,
    RuntimeAttemptError,
    RuntimePreflightError,
    RuntimeSemanticBridgeError,
    RuntimeSessionCreationError,
    RuntimeSessionPlanError,
)
from vocab.runtime.layout import build_layout
from vocab.runtime.lock import DeploymentLock
from vocab.runtime.session_plan import parse_session_plan
from vocab.session import load_session_manifest

from tests.test_runtime import (
    FakeAnki,
    bootstrap_deployment,
    snapshot,
    write_config,
)
from tests.test_runtime_wave_b import ForgeAnki


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


R_UNIT = "subtle::small-difference"
W_UNIT = "meticulous::careful"


def unit_note(note_id: int, unit_key: str, *, targets: str = "R") -> dict[str, object]:
    lemma, sense = unit_key.split("::")
    values = {name: "" for name in NOTE_FIELDS}
    values.update(
        {
            "unit_key": unit_key,
            "lemma": lemma,
            "lemma_slug": lemma,
            "sense_slug": sense,
            "unit_type": "word",
            "register": "neutral",
            "definition_en": f"the sense of {lemma} used here",
            "source_ref": "corpus:bbc:2026-08-01",
            "source_sentence": f"A {lemma} distinction matters here.",
            "Ctx_1": f"A {lemma} distinction matters here.",
            "created": "2026-08-01",
        }
    )
    for channel in targets:
        values[f"Target_{channel}"] = "1"
        values[f"state_{channel}"] = "NEW"
    fields = {
        name: {"value": values[name], "order": index}
        for index, name in enumerate(NOTE_FIELDS)
    }
    return {"noteId": note_id, "modelName": "VocabularyUnit", "fields": fields}


def assessment_anki() -> ForgeAnki:
    return ForgeAnki(
        [
            unit_note(101, R_UNIT, targets="R"),
            unit_note(102, W_UNIT, targets="W"),
        ]
    )


def r_item(unit_key: str = R_UNIT) -> dict[str, object]:
    return {
        "unit_key": unit_key,
        "channel": "R",
        "passage": "The distinction is subtle but consequential.",
        "question": "What does subtle mean here?",
    }


def w_item(unit_key: str = W_UNIT) -> dict[str, object]:
    return {
        "unit_key": unit_key,
        "channel": "W",
        "production_prompt": "Describe a meticulous process you follow.",
        "semantic_constraints": "Use meticulous in its careful sense.",
    }


def plan_bytes(*items: dict[str, object]) -> bytes:
    return json.dumps(
        {"artifact": "vocab.t12.session-plan", "v": 1, "items": list(items)}
    ).encode("utf-8")


def write_plan(tmp_path: Path, *items: dict[str, object]) -> Path:
    path = tmp_path / "plan.json"
    path.write_bytes(plan_bytes(*items))
    return path


def deployment(tmp_path: Path):
    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    return config, build_layout(config.data_root)


def create_session_directly(tmp_path: Path, *items: dict[str, object], anki=None):
    config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    plan = parse_session_plan(plan_bytes(*items))
    result = assessment_session.create_session(
        plan,
        anki=anki if anki is not None else assessment_anki(),
        artifact_store=store,
        session_root=layout.session_root,
    )
    return config, layout, store, result


class RecordingPort:
    """An attempt port that records the exact call order and payload."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.events: list[str] = []
        self.displayed: bytes | None = None
        self.fail_display = False

    def display_stimulus(self, payload: bytes) -> None:
        if self.fail_display:
            raise RuntimeError("display device failed")
        self.events.append("display")
        self.displayed = payload

    def ask_terminal_action(self) -> str:
        self.events.append("ask")
        return self.action


# ----------------------------------------------------------------------
# Session-plan schema
# ----------------------------------------------------------------------


def test_plan_round_trips_and_preserves_order() -> None:
    plan = parse_session_plan(plan_bytes(w_item(), r_item()))
    assert [item.channel for item in plan.items] == ["W", "R"]
    assert plan.items[1].stimulus["passage"].endswith("consequential.")


def test_plan_is_detached_from_the_caller() -> None:
    raw = {"unit_key": R_UNIT, "channel": "R", "passage": "p", "question": "q"}
    plan = parse_session_plan(plan_bytes(raw))
    raw["passage"] = "mutated"
    assert plan.items[0].stimulus["passage"] == "p"
    with pytest.raises(TypeError):
        plan.items[0].stimulus["passage"] = "mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    "raw",
    (
        pytest.param(b'{"artifact":"vocab.t12.session-plan","artifact":"x","v":1,"items":[]}', id="duplicate-key"),
        pytest.param(b'{"artifact":"vocab.t12.session-plan","v":NaN,"items":[]}', id="nan"),
        pytest.param(b'{"artifact":"vocab.t12.session-plan","v":Infinity,"items":[]}', id="infinity"),
        pytest.param(b"not json", id="not-json"),
    ),
)
def test_plan_rejects_non_strict_json(raw: bytes) -> None:
    with pytest.raises(RuntimeSessionPlanError):
        parse_session_plan(raw)


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(lambda p: p.update(v=True), id="bool-v"),
        pytest.param(lambda p: p.update(v=2), id="wrong-v"),
        pytest.param(lambda p: p.update(artifact="other"), id="wrong-artifact"),
        pytest.param(lambda p: p.update(items=[]), id="empty-items"),
        pytest.param(lambda p: p.update(items={}), id="items-not-list"),
        pytest.param(lambda p: p.update(extra=1), id="unknown-top-key"),
        pytest.param(lambda p: p.pop("items"), id="missing-items"),
    ),
)
def test_plan_rejects_top_level_violations(mutate) -> None:
    body = {"artifact": "vocab.t12.session-plan", "v": 1, "items": [r_item()]}
    mutate(body)
    with pytest.raises(RuntimeSessionPlanError):
        parse_session_plan(json.dumps(body).encode("utf-8"))


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(lambda i: i.update(channel="L"), id="channel-L"),
        pytest.param(lambda i: i.update(channel="S"), id="channel-S"),
        pytest.param(lambda i: i.update(channel="X"), id="channel-unknown"),
        pytest.param(lambda i: i.update(extra=1), id="unknown-key"),
        pytest.param(lambda i: i.pop("question"), id="missing-field"),
        pytest.param(lambda i: i.update(passage=""), id="empty-text"),
        pytest.param(lambda i: i.update(passage="   "), id="blank-text"),
        pytest.param(lambda i: i.update(passage=5), id="non-string"),
        pytest.param(lambda i: i.update(unit_key="not a key"), id="bad-unit-key"),
        pytest.param(lambda i: i.update(passage="a\ud800b"), id="unpaired-surrogate"),
    ),
)
def test_plan_rejects_item_violations(mutate) -> None:
    item = r_item()
    mutate(item)
    with pytest.raises(RuntimeSessionPlanError):
        parse_session_plan(plan_bytes(item))


def test_plan_preserves_exact_text_without_normalization() -> None:
    item = r_item()
    item["passage"] = "  spaced \r\n text  "
    item["question"] = "ﬁ ligature and \u00e9\u0301 combining"
    plan = parse_session_plan(plan_bytes(item))
    assert plan.items[0].stimulus["passage"] == "  spaced \r\n text  "
    assert plan.items[0].stimulus["question"] == "ﬁ ligature and \u00e9\u0301 combining"


def test_plan_permits_duplicate_items() -> None:
    plan = parse_session_plan(plan_bytes(r_item(), r_item()))
    assert len(plan.items) == 2


# ----------------------------------------------------------------------
# Stimulus rendering
# ----------------------------------------------------------------------


def test_exact_r_and_w_rendering_bytes() -> None:
    r = parse_session_plan(plan_bytes(r_item())).items[0]
    assert assessment_session.render_stimulus_bytes(r.channel, r.stimulus) == (
        b"The distinction is subtle but consequential."
        b"\n\n"
        b"What does subtle mean here?"
    )
    w = parse_session_plan(plan_bytes(w_item())).items[0]
    assert assessment_session.render_stimulus_bytes(w.channel, w.stimulus) == (
        b"Describe a meticulous process you follow."
        b"\n\n"
        b"Use meticulous in its careful sense."
    )


def test_rendering_adds_no_bom_label_or_trailing_newline() -> None:
    item = parse_session_plan(plan_bytes(r_item())).items[0]
    payload = assessment_session.render_stimulus_bytes(item.channel, item.stimulus)
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert not payload.endswith(b"\n")
    assert b"passage" not in payload and b"question" not in payload


def test_ref_is_derived_before_any_write() -> None:
    import hashlib

    plan = parse_session_plan(plan_bytes(r_item()))
    candidates = assessment_session.derive_candidates(plan)
    payload = candidates[0].stimulus_bytes
    assert candidates[0].expected_ref == (
        "sha256:" + hashlib.sha256(payload).hexdigest()
    )
    assert candidates[0].item["item_ordinal"] == 0


def test_plan_order_becomes_ordinals() -> None:
    plan = parse_session_plan(plan_bytes(w_item(), r_item(), r_item()))
    candidates = assessment_session.derive_candidates(plan)
    assert [c.item["item_ordinal"] for c in candidates] == [0, 1, 2]
    assert [c.item["channel"] for c in candidates] == ["W", "R", "R"]


def test_task_kind_comes_from_the_frozen_contract() -> None:
    candidates = assessment_session.derive_candidates(
        parse_session_plan(plan_bytes(r_item(), w_item()))
    )
    assert candidates[0].item["task_kind"] == "reading_comprehension"
    assert candidates[1].item["task_kind"] == "written_production"


# ----------------------------------------------------------------------
# Session creation
# ----------------------------------------------------------------------


def test_session_creation_publishes_manifest_and_artifacts(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(
        tmp_path, r_item(), w_item()
    )
    assert result.item_count == 2
    manifest = load_session_manifest(layout.session_root, result.session_id)
    decoded = strict_json_loads(manifest.canonical_bytes)
    assert [item["item_ordinal"] for item in decoded["items"]] == [0, 1]
    for item in decoded["items"]:
        assert store.read(item["stimulus_artifact_ref"])


def test_session_manifest_keeps_stimulus_text_verbatim(tmp_path: Path) -> None:
    item = r_item()
    item["passage"] = "  spaced \r\n text  "
    _config, layout, _store, result = create_session_directly(tmp_path, item)
    decoded = strict_json_loads(
        load_session_manifest(layout.session_root, result.session_id).canonical_bytes
    )
    assert decoded["items"][0]["stimulus"]["passage"] == "  spaced \r\n text  "


def test_disabled_channel_rejects_the_whole_session(tmp_path: Path) -> None:
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    before = snapshot(layout.data_root)
    plan = parse_session_plan(plan_bytes(r_item(W_UNIT)))
    with pytest.raises(RuntimeSessionCreationError, match="enables"):
        assessment_session.create_session(
            plan,
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert snapshot(layout.data_root) == before


def test_unknown_unit_rejects_the_whole_session(tmp_path: Path) -> None:
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    before = snapshot(layout.data_root)
    plan = parse_session_plan(plan_bytes(r_item("ghost::demo")))
    with pytest.raises(RuntimeSessionCreationError, match="not in the active registry"):
        assessment_session.create_session(
            plan,
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert snapshot(layout.data_root) == before


def test_invalid_later_item_writes_zero_artifacts(tmp_path: Path) -> None:
    """Every Unit is validated before the first ArtifactStore.put."""
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    before = snapshot(layout.data_root)
    plan = parse_session_plan(plan_bytes(r_item(), r_item(W_UNIT)))
    with pytest.raises(RuntimeSessionCreationError):
        assessment_session.create_session(
            plan,
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert snapshot(layout.data_root) == before


def test_registry_validation_precedes_artifact_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    events: list[str] = []
    real_registry = assessment_session.read_registry
    real_put = ArtifactStore.put

    monkeypatch.setattr(
        assessment_session,
        "read_registry",
        lambda anki: (events.append("registry"), real_registry(anki))[1],
    )
    monkeypatch.setattr(
        ArtifactStore,
        "put",
        lambda self, data: (events.append("put"), real_put(self, data))[1],
    )
    assessment_session.create_session(
        parse_session_plan(plan_bytes(r_item())),
        anki=assessment_anki(),
        artifact_store=store,
        session_root=layout.session_root,
    )
    assert events.index("registry") < events.index("put")


def test_created_at_is_captured_exactly_once_in_utc(tmp_path: Path) -> None:
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    calls: list[str] = []

    def clock() -> str:
        calls.append("2026-08-30T12:00:00+00:00")
        return calls[-1]

    result = assessment_session.create_session(
        parse_session_plan(plan_bytes(r_item(), w_item())),
        anki=assessment_anki(),
        artifact_store=store,
        session_root=layout.session_root,
        clock=clock,
    )
    assert len(calls) == 1
    assert result.created_at == "2026-08-30T12:00:00+00:00"


def test_production_clock_is_utc_and_ignores_local_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    stamp = assessment_session.utc_clock()
    assert stamp.endswith("+00:00")


def test_ref_mismatch_blocks_manifest_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    monkeypatch.setattr(
        ArtifactStore, "put", lambda self, data: "sha256:" + "0" * 64
    )
    with pytest.raises(RuntimeSessionCreationError, match="does not match the derived ref"):
        assessment_session.create_session(
            parse_session_plan(plan_bytes(r_item())),
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert not any(layout.session_root.iterdir())


def test_late_failure_leaves_inert_orphan_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rollback is attempted; orphans are content-addressed and inert."""
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    from vocab import session as session_module

    monkeypatch.setattr(
        assessment_session,
        "persist_session_manifest",
        lambda root, manifest: (_ for _ in ()).throw(
            session_module.SessionManifestError("disk failed")
        ),
    )
    with pytest.raises(RuntimeSessionCreationError):
        assessment_session.create_session(
            parse_session_plan(plan_bytes(r_item())),
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert list(layout.artifact_root.iterdir())
    assert not any(layout.session_root.iterdir())


# ----------------------------------------------------------------------
# Fresh attempt
# ----------------------------------------------------------------------


def test_attempt_order_is_display_consume_then_action(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    response = tmp_path / "response.txt"
    response.write_bytes("subtle means hard to notice".encode("utf-8"))
    port = RecordingPort(attempt_runner.SUBMIT)

    outcome = attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=port,
        response_file=response,
    )
    assert port.events == ["display", "ask"]
    assert port.displayed == (
        b"The distinction is subtle but consequential."
        b"\n\nWhat does subtle mean here?"
    )
    assert outcome.action == "SUBMIT"
    assert outcome.channel == "R"


@pytest.mark.parametrize("action", ("SKIP", "REFUSE"))
def test_response_file_is_unread_before_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """SKIP and REFUSE must never open the transport file."""
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    response = tmp_path / "response.txt"
    response.write_bytes(b"never read")

    reads: list[str] = []
    real_read = Path.read_bytes

    def counted(self: Path) -> bytes:
        if self == response:
            reads.append("read")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    outcome = attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(action),
        response_file=response,
    )
    assert outcome.action == action
    assert reads == []


def test_submit_reads_the_response_file_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    response = tmp_path / "response.txt"
    payload = "exact bytes \r\n preserved".encode("utf-8")
    response.write_bytes(payload)

    reads: list[str] = []
    real_read = Path.read_bytes

    def counted(self: Path) -> bytes:
        if self == response:
            reads.append("read")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", counted)

    captured: list[bytes] = []
    real_close = attempt_runner.close_text_submission

    def watching(**kwargs):
        captured.append(kwargs["raw_bytes"])
        return real_close(**kwargs)

    monkeypatch.setattr(attempt_runner, "close_text_submission", watching)

    attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.SUBMIT),
        response_file=response,
    )
    assert reads == ["read"]
    assert captured == [payload]


def test_submit_without_a_response_file_fails_closed(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    with pytest.raises(RuntimeAttemptError, match="never reinterpreted"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=RecordingPort(attempt_runner.SUBMIT),
            response_file=None,
        )


@pytest.mark.parametrize(
    "corrupt",
    (
        pytest.param("mutate", id="artifact-text-mismatch"),
        pytest.param("remove", id="artifact-missing"),
    ),
)
def test_corrupt_stimulus_artifact_prevents_reservation(
    tmp_path: Path, corrupt: str
) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    decoded = strict_json_loads(
        load_session_manifest(layout.session_root, result.session_id).canonical_bytes
    )
    ref = decoded["items"][0]["stimulus_artifact_ref"]
    target = layout.artifact_root / ref.removeprefix("sha256:")
    if corrupt == "mutate":
        target.write_bytes(b"different bytes entirely")
    else:
        target.unlink()

    exposure_before = layout.exposure_path.read_bytes()
    port = RecordingPort(attempt_runner.SKIP)
    with pytest.raises(RuntimeAttemptError):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=port,
        )
    assert port.events == []
    assert layout.exposure_path.read_bytes() == exposure_before


def test_display_failure_leaves_no_terminal_receipt(tmp_path: Path) -> None:
    """Reservation is durable, but no capture or disposition is synthesized."""
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    port = RecordingPort(attempt_runner.SKIP)
    port.fail_display = True

    capture_before = layout.capture_path.read_bytes()
    disposition_before = layout.disposition_path.read_bytes()
    with pytest.raises(RuntimeError, match="display device failed"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=port,
        )
    assert port.events == []
    assert layout.capture_path.read_bytes() == capture_before
    assert layout.disposition_path.read_bytes() == disposition_before
    assert layout.exposure_path.read_bytes() != b""


def test_abandoned_reservation_cannot_be_redisplayed(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    port = RecordingPort(attempt_runner.SKIP)
    port.fail_display = True
    with pytest.raises(RuntimeError):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=port,
        )
    retry = RecordingPort(attempt_runner.SKIP)
    with pytest.raises(RuntimeAttemptError, match="exposure could not be reserved"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=retry,
        )
    assert retry.events == []


def test_unknown_terminal_action_is_refused(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    with pytest.raises(RuntimeAttemptError, match="terminal action must be"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=RecordingPort("MAYBE"),
        )


def test_permit_is_never_serialized() -> None:
    source = Path("vocab/runtime/attempt_runner.py").read_text(encoding="utf-8")
    for token in ("pickle", "json.dumps(permit", "permit.__dict__", "DisplayPermit("):
        assert token not in source


# ----------------------------------------------------------------------
# CLI shape
# ----------------------------------------------------------------------


def test_cli_offers_no_preselected_skip_or_refuse() -> None:
    parser = cli.build_parser()
    base = ["attempt-run", "--config", "c", "--session-id", "s", "--item-ordinal", "0"]
    parser.parse_args(base)
    for flag in ("--skip", "--refuse"):
        with pytest.raises(SystemExit):
            parser.parse_args(base + [flag])


def test_cli_requires_explicit_selectors() -> None:
    parser = cli.build_parser()
    for argv in (
        ["attempt-run", "--config", "c", "--session-id", "s"],
        ["attempt-run", "--config", "c", "--item-ordinal", "0"],
        ["semantic-export", "--config", "c", "--session-id", "s"],
        ["assess", "--config", "c", "--session-id", "s", "--item-ordinal", "0"],
        ["session-create", "--config", "c"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_cli_assess_path_is_a_closed_choice() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "assess", "--config", "c", "--session-id", "s",
                "--item-ordinal", "0", "--path", "guess",
            ]
        )


def test_cli_session_create_runs_under_write_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_deployment(tmp_path, FakeAnki([]))
    plan_path = write_plan(tmp_path, r_item())
    events: list[str] = []
    from vocab.runtime import operation

    real_identity = operation.read_identity
    real_acquire = DeploymentLock.acquire
    real_preflight = operation.run_runtime_write_preflight

    monkeypatch.setattr(
        operation,
        "read_identity",
        lambda p: (events.append("identity"), real_identity(p))[1],
    )
    monkeypatch.setattr(
        DeploymentLock,
        "acquire",
        lambda self: (real_acquire(self), events.append("lock"))[0],
    )
    monkeypatch.setattr(
        operation,
        "run_runtime_write_preflight",
        lambda *a, **k: (events.append("preflight"), real_preflight(*a, **k))[1],
    )
    real_create = assessment_session.create_session
    monkeypatch.setattr(
        assessment_session,
        "create_session",
        lambda *a, **k: (events.append("operation"), real_create(*a, **k))[1],
    )
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_SUCCESS
    assert events == ["identity", "lock", "preflight", "operation"]


def test_cli_session_create_refuses_on_held_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    layout = build_layout(config.data_root)
    plan_path = write_plan(tmp_path, r_item())
    holder = DeploymentLock(layout.lock_path)
    holder.acquire()
    before = snapshot(config.data_root)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_LOCK_CONTENTION
    assert snapshot(config.data_root) == before
    holder.release()


def test_cli_session_create_refuses_when_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    layout = build_layout(config.data_root)
    plan_path = write_plan(tmp_path, r_item())
    shutil.rmtree(layout.artifact_root)
    before = snapshot(config.data_root)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_REFUSED
    assert snapshot(config.data_root) == before
    assert not layout.artifact_root.exists()


def test_artifact_store_gate_refuses_a_missing_root(tmp_path: Path) -> None:
    import shutil

    config, layout = deployment(tmp_path)
    shutil.rmtree(layout.artifact_root)
    with pytest.raises(RuntimePreflightError, match="only bootstrap may create it"):
        open_deployment_artifact_store(layout)
    assert not layout.artifact_root.exists()


# ----------------------------------------------------------------------
# Static authority
# ----------------------------------------------------------------------


WAVE_C_SOURCES = (
    "vocab/runtime/session_plan.py",
    "vocab/runtime/assessment_session.py",
    "vocab/runtime/attempt_runner.py",
    "vocab/runtime/semantic_bridge.py",
    "vocab/runtime/artifact_store_gate.py",
)


@pytest.mark.parametrize("relative", WAVE_C_SOURCES + ("vocab/cli.py",))
def test_c_static_no_journal_bypass(relative: str) -> None:
    source = Path(relative).read_text(encoding="utf-8")
    assert "from ..events import" not in source
    assert "from .events import" not in source
    assert "import vocab.events" not in source
    assert "EventLog(" not in source
    assert "open_existing" not in source
    assert ".log(" not in source


@pytest.mark.parametrize("relative", WAVE_C_SOURCES)
def test_c_static_no_lifecycle_mutation(relative: str) -> None:
    source = Path(relative).read_text(encoding="utf-8")
    for token in ("reconcile_unit", "STATE", "suspend", "unsuspend", "state_"):
        assert token not in source, f"{relative} references {token!r}"


@pytest.mark.parametrize("relative", WAVE_C_SOURCES)
def test_c_static_no_provider_or_credential_path(relative: str) -> None:
    source = Path(relative).read_text(encoding="utf-8")
    for token in (
        "openai", "anthropic", "api.openai.com", "api.anthropic.com",
        "OPENAI_API_KEY", "requests.post", "urllib.request", "httpx",
        "os.environ", "getenv", "webbrowser",
    ):
        assert token not in source, f"{relative} references {token!r}"


@pytest.mark.parametrize("relative", WAVE_C_SOURCES + ("vocab/cli.py",))
def test_c_static_no_broad_exception_handling(relative: str) -> None:
    source = Path(relative).read_text(encoding="utf-8")
    assert "except Exception" not in source
    assert "except BaseException" not in source
    assert "except ValueError" not in source


def test_c_static_no_exit_four_in_wave_c_commands() -> None:
    source = Path("vocab/cli.py").read_text(encoding="utf-8")
    for command in ("_command_session_create", "_command_attempt_run",
                    "_command_semantic_export", "_command_assess"):
        start = source.index(f"def {command}(")
        end = source.index("\ndef ", start + 1)
        assert "EXIT_ITEM_FAILURES" not in source[start:end]


def test_c_static_import_allowlist_still_two_paths() -> None:
    import sys

    sys.path.insert(0, "tests")
    from t12_ast_invariants import (
        APPROVED_EVENT_LOG_ACQUISITIONS,
        APPROVED_EVENT_LOG_CONSTRUCTORS,
        CONCRETE_EVENT_IMPORT_ALLOWLIST,
        assert_t12_ast_invariants,
    )

    assert CONCRETE_EVENT_IMPORT_ALLOWLIST == {
        "vocab/assessment_producer.py",
        "vocab/runtime/eventlog_authority.py",
    }
    assert APPROVED_EVENT_LOG_CONSTRUCTORS == frozenset()
    assert len(APPROVED_EVENT_LOG_ACQUISITIONS) == 1
    assert_t12_ast_invariants(Path(".").resolve())


# ----------------------------------------------------------------------
# Semantic path and final JUDGE
# ----------------------------------------------------------------------


def captured_attempt(tmp_path: Path, item: dict[str, object], response: bytes):
    """Create a session, run one SUBMIT attempt, and return the durable state."""
    _config, layout, store, result = create_session_directly(tmp_path, item)
    path = tmp_path / "response.txt"
    path.write_bytes(response)
    outcome = attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.SUBMIT),
        response_file=path,
    )
    return layout, store, result, outcome


def open_journal(layout):
    from vocab.runtime.eventlog_authority import open_runtime_event_log

    return open_runtime_event_log(layout.event_log_path)


def journal_events(layout):
    return open_journal(layout).read_strict()


def test_semantic_export_binds_request_ref_to_its_digest(tmp_path: Path) -> None:
    layout, store, result, outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    assert export.attempt_id == outcome.attempt_id
    assert export.request_ref == f"sha256:{export.request_digest}"
    assert store.read(export.request_ref) == export.canonical_bytes


def test_semantic_export_requires_a_captured_attempt(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    with pytest.raises(RuntimeAssessmentError):
        semantic_bridge.export_semantic_request(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            anki=assessment_anki(),
        )


def test_w_target_absent_forbids_semantic_export(tmp_path: Path) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, w_item(), "I work carefully and slowly.".encode("utf-8")
    )
    with pytest.raises(RuntimeSemanticBridgeError, match="OMITTED path applies"):
        semantic_bridge.export_semantic_request(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            anki=assessment_anki(),
        )


def test_w_target_absent_uses_the_deterministic_omitted_path(tmp_path: Path) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, w_item(), "I work carefully and slowly.".encode("utf-8")
    )
    emitted = semantic_bridge.emit_omitted_assessment(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
        open_event_log=lambda: open_journal(layout),
    )
    assert emitted.path == "omitted"
    assert emitted.appended == 1

    history = open_journal(layout).read_strict()
    judges = [event for event in history if event.event == "JUDGE"]
    assert len(judges) == 1
    assert judges[0].payload["outcome"] == "OMITTED"


def test_w_target_present_forbids_the_omitted_path(tmp_path: Path) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, w_item(), "A meticulous checklist keeps me honest.".encode("utf-8")
    )
    with pytest.raises(RuntimeAssessmentError, match="present"):
        semantic_bridge.emit_omitted_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=lambda: open_journal(layout),
        )


def test_policy_disposition_path_emits_a_policy_judge(tmp_path: Path) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.REFUSE),
    )
    emitted = semantic_bridge.emit_policy_assessment(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
        open_event_log=lambda: open_journal(layout),
    )
    assert emitted.path == "policy"
    judges = [e for e in open_journal(layout).read_strict() if e.event == "JUDGE"]
    assert len(judges) == 1
    assert judges[0].payload["outcome"] == "ABSTAIN"


def proposal_for(
    export, *, verdict: str = "PASS", failure_code: str = ""
) -> bytes:
    """Build a valid semantic proposal transport file for one exported request."""
    from vocab.semantic_response import (
        SEMANTIC_RESPONSE_ARTIFACT,
        SEMANTIC_RESPONSE_VERSION,
    )

    return json.dumps(
        {
            "artifact": SEMANTIC_RESPONSE_ARTIFACT,
            "v": SEMANTIC_RESPONSE_VERSION,
            "request_digest": export.request_digest,
            "outcome": verdict,
            "failure_code": failure_code,
            "reason_code": "",
            "semantic_rationale": "The response conveys the intended sense.",
        }
    ).encode("utf-8")


def run_semantic(tmp_path: Path, *, decision: str = "APPROVE", verdict: str = "PASS"):
    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    emitted = semantic_bridge.emit_semantic_assessment(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        request_ref=export.request_ref,
        proposal_bytes=proposal_for(export, verdict=verdict),
        assessor_id="chatgpt-plus",
        assessor_version="2026-08",
        reviewer_id="lam",
        reviewer_version=1,
        decision=decision,
        artifact_store=store,
        anki=assessment_anki(),
        open_event_log=lambda: open_journal(layout),
    )
    return layout, store, result, export, emitted


def test_semantic_path_emits_one_judge(tmp_path: Path) -> None:
    layout, store, _result, export, emitted = run_semantic(tmp_path)
    assert emitted.path == "semantic"
    assert emitted.appended == 1
    judges = [e for e in open_journal(layout).read_strict() if e.event == "JUDGE"]
    assert len(judges) == 1
    assert judges[0].payload["outcome"] == "PASS"
    assert store.read(export.request_ref)


def test_proposal_ref_binds_to_its_response_digest(tmp_path: Path) -> None:
    """The canonical proposal must be stored at sha256:<response_digest>."""
    from vocab.semantic_response import (
        canonical_semantic_proposal_bytes,
        import_semantic_response,
    )

    layout, store, _result, export, _emitted = run_semantic(tmp_path)
    imported = import_semantic_response(
        proposal_for(export),
        request=strict_json_loads(export.canonical_bytes),
        assessor_id="chatgpt-plus",
        assessor_version="2026-08",
    )
    expected_ref = f"sha256:{imported.response_digest}"
    assert store.read(expected_ref) == canonical_semantic_proposal_bytes(imported)
    assert store.read(export.request_ref) == export.canonical_bytes


def test_producer_rerun_appends_zero(tmp_path: Path) -> None:
    layout, store, result, export, first = run_semantic(tmp_path)
    again = semantic_bridge.emit_semantic_assessment(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        request_ref=export.request_ref,
        proposal_bytes=proposal_for(export),
        assessor_id="chatgpt-plus",
        assessor_version="2026-08",
        reviewer_id="lam",
        reviewer_version=1,
        decision="APPROVE",
        artifact_store=store,
        anki=assessment_anki(),
        open_event_log=lambda: open_journal(layout),
    )
    assert first.appended == 1
    assert again.appended == 0
    judges = [e for e in open_journal(layout).read_strict() if e.event == "JUDGE"]
    assert len(judges) == 1


def test_producer_conflict_fails_closed(tmp_path: Path) -> None:
    layout, store, result, export, _first = run_semantic(tmp_path)
    with pytest.raises(RuntimeAssessmentError):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=export.request_ref,
            proposal_bytes=proposal_for(
                export, verdict="FAIL", failure_code="wrong_meaning"
            ),
            assessor_id="chatgpt-plus",
            assessor_version="2026-08",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=lambda: open_journal(layout),
        )
    judges = [e for e in open_journal(layout).read_strict() if e.event == "JUDGE"]
    assert len(judges) == 1
    assert judges[0].payload["outcome"] == "PASS"


def test_rejected_review_follows_the_existing_policy(tmp_path: Path) -> None:
    layout, _store, _result, _export, emitted = run_semantic(
        tmp_path, decision="REJECT"
    )
    judges = [e for e in open_journal(layout).read_strict() if e.event == "JUDGE"]
    assert len(judges) == 1
    assert judges[0].payload["outcome"] == "ABSTAIN"
    assert emitted.path == "semantic"


def test_foreign_request_ref_fails_before_emission(tmp_path: Path) -> None:
    layout, store, result, export, _emitted = run_semantic(tmp_path)
    foreign = store.put(b"not a semantic request")
    with pytest.raises(RuntimeSemanticBridgeError):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=foreign,
            proposal_bytes=proposal_for(export),
            assessor_id="a",
            assessor_version="1",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=lambda: open_journal(layout),
        )


def test_assessment_never_mutates_lifecycle(tmp_path: Path) -> None:
    """T12 evidence production must not touch T9 state."""
    layout, _store, _result, _export, _emitted = run_semantic(tmp_path)
    history = open_journal(layout).read_strict()
    assert {event.event for event in history} == {"JUDGE"}
    assert not any(event.event == "STATE" for event in history)


# ----------------------------------------------------------------------
# B1: the session plan is unread until write authority is established
# ----------------------------------------------------------------------


def trace_plan_read(monkeypatch: pytest.MonkeyPatch, plan_path: Path) -> list[str]:
    """Record the real order of write authority and plan handling.

    Instrumentation starts at Path.read_bytes rather than at read_identity, so
    a plan read that happens *before* identity is still visible in the trace.
    """
    events: list[str] = []
    from vocab.runtime import operation

    real_identity = operation.read_identity
    real_acquire = DeploymentLock.acquire
    real_preflight = operation.run_runtime_write_preflight
    real_parse = cli.parse_session_plan
    real_create = assessment_session.create_session
    real_read = Path.read_bytes

    def traced_read(self: Path) -> bytes:
        if self == plan_path:
            events.append("plan-read")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", traced_read)
    monkeypatch.setattr(
        operation,
        "read_identity",
        lambda p: (events.append("identity"), real_identity(p))[1],
    )
    monkeypatch.setattr(
        DeploymentLock,
        "acquire",
        lambda self: (real_acquire(self), events.append("lock"))[0],
    )
    monkeypatch.setattr(
        operation,
        "run_runtime_write_preflight",
        lambda *a, **k: (events.append("preflight"), real_preflight(*a, **k))[1],
    )
    monkeypatch.setattr(
        cli,
        "parse_session_plan",
        lambda raw: (events.append("plan-parse"), real_parse(raw))[1],
    )
    monkeypatch.setattr(
        assessment_session,
        "create_session",
        lambda *a, **k: (events.append("operation"), real_create(*a, **k))[1],
    )
    return events


def test_b1_successful_order_is_identity_lock_preflight_then_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_deployment(tmp_path, FakeAnki([]))
    plan_path = write_plan(tmp_path, r_item())
    events = trace_plan_read(monkeypatch, plan_path)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_SUCCESS
    assert events == [
        "identity", "lock", "preflight", "plan-read", "plan-parse", "operation"
    ]


def test_b1_missing_identity_leaves_the_plan_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(tmp_path)
    plan_path = write_plan(tmp_path, r_item())
    events = trace_plan_read(monkeypatch, plan_path)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_REFUSED
    assert "plan-read" not in events and "plan-parse" not in events


def test_b1_lock_contention_leaves_the_plan_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    layout = build_layout(config.data_root)
    plan_path = write_plan(tmp_path, r_item())
    holder = DeploymentLock(layout.lock_path)
    holder.acquire()
    events = trace_plan_read(monkeypatch, plan_path)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_LOCK_CONTENTION
    assert "plan-read" not in events and "plan-parse" not in events
    holder.release()


def test_b1_failed_preflight_leaves_the_plan_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    shutil.rmtree(build_layout(config.data_root).artifact_root)
    plan_path = write_plan(tmp_path, r_item())
    events = trace_plan_read(monkeypatch, plan_path)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_REFUSED
    assert "preflight" in events
    assert "plan-read" not in events and "plan-parse" not in events


# ----------------------------------------------------------------------
# B2: notesInfo must return the exact requested noteId
# ----------------------------------------------------------------------


def test_b2_foreign_note_id_fails_before_any_artifact_put(tmp_path: Path) -> None:
    """A structurally valid note carrying a different noteId is a different Unit."""
    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    anki = assessment_anki()
    real_notes_info = anki.notes_info

    def swapped(note_ids):
        notes = real_notes_info(note_ids)
        if len(list(note_ids)) != 1:
            return notes
        swapped_note = json.loads(json.dumps(notes[0]))
        swapped_note["noteId"] = 999999
        return [swapped_note]

    anki.notes_info = swapped  # type: ignore[assignment]
    before = snapshot(layout.data_root)

    with pytest.raises(RuntimeSessionCreationError, match="not the requested"):
        assessment_session.create_session(
            parse_session_plan(plan_bytes(r_item())),
            anki=anki,
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert snapshot(layout.data_root) == before


def test_b2_note_type_comes_from_the_contract_constant() -> None:
    source = Path("vocab/runtime/assessment_session.py").read_text(encoding="utf-8")
    assert '"VocabularyUnit"' not in source
    assert "ANKI_NOTE_TYPE_NAME" in source


# ----------------------------------------------------------------------
# B3: named operational families become refusals, not tracebacks
# ----------------------------------------------------------------------


def assess_via_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str,
                   session_id: str, anki) -> int:
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: anki)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    return cli.main(
        ["assess", "--config", str(tmp_path / "runtime.json"),
         "--session-id", session_id, "--item-ordinal", "0", "--path", path]
    )


def test_b3_corrupted_disposition_history_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.REFUSE),
    )
    layout.disposition_path.write_bytes(b'{"torn": true}\n')
    assert assess_via_cli(
        tmp_path, monkeypatch, "policy", result.session_id, assessment_anki()
    ) == cli.EXIT_REFUSED


def test_b3_producer_conflict_is_a_refusal_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, store, result, export, _first = run_semantic(tmp_path)
    proposal = tmp_path / "conflict.json"
    proposal.write_bytes(
        proposal_for(export, verdict="FAIL", failure_code="wrong_meaning")
    )
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert cli.main(
        ["assess", "--config", str(tmp_path / "runtime.json"),
         "--session-id", result.session_id, "--item-ordinal", "0",
         "--path", "semantic", "--request-ref", export.request_ref,
         "--proposal", str(proposal), "--assessor-id", "chatgpt-plus",
         "--assessor-version", "2026-08", "--reviewer-id", "lam",
         "--reviewer-version", "1", "--decision", "APPROVE"]
    ) == cli.EXIT_REFUSED


def test_b3_channel_no_longer_enabled_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Unit's current state is authority; a disabled channel refuses."""
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    attempt_runner.run_fresh_attempt(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.REFUSE),
    )
    disabled = ForgeAnki(
        [unit_note(101, R_UNIT, targets="W"), unit_note(102, W_UNIT, targets="W")]
    )
    assert assess_via_cli(
        tmp_path, monkeypatch, "policy", result.session_id, disabled
    ) == cli.EXIT_REFUSED


# ----------------------------------------------------------------------
# B4: request_ref must be canonical and attempt-bound before proposal write
# ----------------------------------------------------------------------


def artifact_names(layout) -> set[str]:
    return {p.name for p in layout.artifact_root.iterdir() if p.is_file()}


def test_b4_request_from_another_attempt_is_rejected_before_any_write(
    tmp_path: Path
) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    other = r_item()
    other["question"] = "A completely different question?"
    plan = parse_session_plan(plan_bytes(other))
    second = assessment_session.create_session(
        plan,
        anki=assessment_anki(),
        artifact_store=store,
        session_root=layout.session_root,
    )
    path = tmp_path / "other-response.txt"
    path.write_bytes(b"another answer")
    attempt_runner.run_fresh_attempt(
        layout,
        session_id=second.session_id,
        item_ordinal=0,
        artifact_store=store,
        port=RecordingPort(attempt_runner.SUBMIT),
        response_file=path,
    )
    foreign = semantic_bridge.export_semantic_request(
        layout,
        session_id=second.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )

    before = artifact_names(layout)
    with pytest.raises(RuntimeSemanticBridgeError, match="canonical request"):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=foreign.request_ref,
            proposal_bytes=proposal_for(foreign),
            assessor_id="a",
            assessor_version="1",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=lambda: open_journal(layout),
        )
    assert artifact_names(layout) == before
    assert not any(e.event == "JUDGE" for e in journal_events(layout))


def test_b4_non_canonical_request_bytes_are_rejected_before_any_write(
    tmp_path: Path
) -> None:
    """Equivalent JSON that is not the canonical serialization must fail."""
    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    reordered = json.dumps(
        strict_json_loads(export.canonical_bytes), indent=1
    ).encode("utf-8")
    assert reordered != export.canonical_bytes
    non_canonical_ref = store.put(reordered)

    before = artifact_names(layout)
    with pytest.raises(RuntimeSemanticBridgeError, match="canonical request"):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=non_canonical_ref,
            proposal_bytes=proposal_for(export),
            assessor_id="a",
            assessor_version="1",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=lambda: open_journal(layout),
        )
    assert artifact_names(layout) == before
    assert not any(e.event == "JUDGE" for e in journal_events(layout))


def test_b4_correct_canonical_request_still_succeeds(tmp_path: Path) -> None:
    layout, _store, _result, _export, emitted = run_semantic(tmp_path)
    assert emitted.path == "semantic"
    assert emitted.appended == 1


# ----------------------------------------------------------------------
# B5: the journal is acquired only after planning
# ----------------------------------------------------------------------


def trace_journal(layout, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    real_plan = semantic_bridge.plan_text_judge
    real_policy = semantic_bridge.plan_policy_judge

    monkeypatch.setattr(
        semantic_bridge,
        "plan_text_judge",
        lambda **k: (events.append("plan"), real_plan(**k))[1],
    )
    monkeypatch.setattr(
        semantic_bridge,
        "plan_policy_judge",
        lambda **k: (events.append("plan"), real_policy(**k))[1],
    )
    real_emit = semantic_bridge.emit_planned_judge
    monkeypatch.setattr(
        semantic_bridge,
        "emit_planned_judge",
        lambda **k: (events.append("emit"), real_emit(**k))[1],
    )

    def watched_open():
        events.append("journal")
        return open_journal(layout)

    return events, watched_open  # type: ignore[return-value]


def test_b5_successful_path_plans_before_acquiring_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    events, watched_open = trace_journal(layout, monkeypatch)
    semantic_bridge.emit_semantic_assessment(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        request_ref=export.request_ref,
        proposal_bytes=proposal_for(export),
        assessor_id="chatgpt-plus",
        assessor_version="2026-08",
        reviewer_id="lam",
        reviewer_version=1,
        decision="APPROVE",
        artifact_store=store,
        anki=assessment_anki(),
        open_event_log=watched_open,
    )
    assert events == ["plan", "journal", "emit"]


def test_b5_invalid_request_never_reaches_journal_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    foreign = store.put(b"not a semantic request at all")
    events, watched_open = trace_journal(layout, monkeypatch)
    with pytest.raises(RuntimeSemanticBridgeError):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=foreign,
            proposal_bytes=proposal_for(export),
            assessor_id="a",
            assessor_version="1",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=watched_open,
        )
    assert "journal" not in events


def test_b5_planner_rejection_never_reaches_journal_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vocab.assessment_planning import AssessmentPlanningError

    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    export = semantic_bridge.export_semantic_request(
        layout,
        session_id=result.session_id,
        item_ordinal=0,
        artifact_store=store,
        anki=assessment_anki(),
    )
    events, watched_open = trace_journal(layout, monkeypatch)

    def rejecting(**kwargs):
        events.append("plan")
        raise AssessmentPlanningError("planner rejected this evidence")

    monkeypatch.setattr(semantic_bridge, "plan_text_judge", rejecting)
    with pytest.raises(RuntimeAssessmentError):
        semantic_bridge.emit_semantic_assessment(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            request_ref=export.request_ref,
            proposal_bytes=proposal_for(export),
            assessor_id="chatgpt-plus",
            assessor_version="2026-08",
            reviewer_id="lam",
            reviewer_version=1,
            decision="APPROVE",
            artifact_store=store,
            anki=assessment_anki(),
            open_event_log=watched_open,
        )
    assert events == ["plan"]
    assert "journal" not in events


# ----------------------------------------------------------------------
# Disposition-ledger normalization scope
# ----------------------------------------------------------------------


def test_disposition_family_only_where_the_core_can_raise_it() -> None:
    """The named family is authorized per seam, never as a global tuple.

    Only vocab/disposition_ledger.py and vocab/response_capture.py raise it, so
    a seam wrapping an operation that cannot reach either must not catch it.
    """
    from vocab.disposition_ledger import DispositionLedgerError
    from vocab.runtime import normalize

    reaches_disposition = (
        normalize.LEDGER_SEAM,      # validate_t12_histories / initialize ledgers
        normalize.ATTEMPT_SEAM,     # reserve_exposure, close/skip/refuse
        normalize.EVIDENCE_SEAM,    # load_validated_*_evidence
        normalize.PRODUCER_SEAM,    # emit_planned_judge
    )
    for seam in reaches_disposition:
        assert DispositionLedgerError in seam

    cannot_reach_disposition = (
        normalize.TRANSCRIPTION_SEAM,
        normalize.MANIFEST_SEAM,
        normalize.ARTIFACT_SEAM,
        normalize.ANKI_SEAM,
        normalize.CORPUS_SEAM,
        normalize.FILESYSTEM_SEAM,
        normalize.PRESENCE_SEAM,
    )
    for seam in cannot_reach_disposition:
        assert DispositionLedgerError not in seam


def test_no_global_operational_exception_tuple() -> None:
    from vocab.runtime import normalize

    assert not hasattr(normalize, "OPERATIONAL_EXCEPTIONS")
    named = [
        value
        for name, value in vars(normalize).items()
        if name.endswith("_SEAM") and isinstance(value, tuple)
    ]
    assert named
    for seam in named:
        assert ValueError not in seam
        assert Exception not in seam
        assert BaseException not in seam


def test_corrupt_disposition_ledger_is_a_preflight_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D70 section 13 validates the whole triple, so this is fail-closed."""
    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    layout = build_layout(config.data_root)
    layout.disposition_path.write_bytes(b'{"torn": true}\n')
    before = snapshot(config.data_root)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(write_plan(tmp_path, r_item()))]
    ) == cli.EXIT_REFUSED
    assert snapshot(config.data_root) == before


def test_bare_value_error_still_surfaces_at_a_disposition_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widening one named family must not make ValueError operational."""
    _config, layout, store, result = create_session_directly(tmp_path, r_item())

    def defect(**kwargs):
        raise ValueError("synthetic defect at the capture seam")

    monkeypatch.setattr(attempt_runner, "record_explicit_skip", defect)
    with pytest.raises(ValueError, match="synthetic defect"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=RecordingPort(attempt_runner.SKIP),
        )


# ----------------------------------------------------------------------
# validate_unit_evidence normalization scope
# ----------------------------------------------------------------------


def test_unit_evidence_failure_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vocab.assessment_evidence import AssessmentEvidenceError

    bootstrap_deployment(tmp_path, FakeAnki([]))
    plan_path = write_plan(tmp_path, r_item())
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr(
        assessment_session,
        "validate_unit_evidence",
        lambda unit: (_ for _ in ()).throw(
            AssessmentEvidenceError("Unit evidence is inconsistent")
        ),
    )
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_REFUSED


@pytest.mark.parametrize(
    "defect",
    (
        pytest.param(TypeError("bad argument"), id="TypeError"),
        pytest.param(ValueError("synthetic bare defect"), id="bare-ValueError"),
    ),
)
def test_unit_evidence_defects_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: BaseException
) -> None:
    """Only the named family is operational; a defect must traceback."""
    bootstrap_deployment(tmp_path, FakeAnki([]))
    plan_path = write_plan(tmp_path, r_item())
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())

    def raising(unit):
        raise defect

    monkeypatch.setattr(assessment_session, "validate_unit_evidence", raising)
    with pytest.raises(type(defect)):
        cli.main(
            ["session-create", "--config", str(tmp_path / "runtime.json"),
             "--plan", str(plan_path)]
        )


def test_unit_evidence_seam_excludes_type_error() -> None:
    from vocab.runtime.normalize import UNIT_EVIDENCE_SEAM

    assert TypeError not in UNIT_EVIDENCE_SEAM
    assert ValueError not in UNIT_EVIDENCE_SEAM
    for source in ("vocab/runtime/assessment_session.py",
                   "vocab/runtime/semantic_bridge.py"):
        assert "(TypeError,)" not in Path(source).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# CLI artifact error ownership
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv_builder", "expected"),
    (
        pytest.param(
            lambda tmp, s: ["session-create", "--config", str(tmp / "runtime.json"),
                            "--plan", str(tmp / "absent-plan.json")],
            "RuntimeSessionPlanError",
            id="session-plan-read",
        ),
        pytest.param(
            lambda tmp, s: ["assess", "--config", str(tmp / "runtime.json"),
                            "--session-id", s, "--item-ordinal", "0",
                            "--path", "semantic",
                            "--request-ref", "sha256:" + "0" * 64,
                            "--proposal", str(tmp / "absent-proposal.json"),
                            "--assessor-id", "a", "--assessor-version", "1",
                            "--reviewer-id", "lam", "--reviewer-version", "1",
                            "--decision", "APPROVE"],
            "RuntimeSemanticBridgeError",
            id="semantic-proposal-read",
        ),
    ),
)
def test_wave_c_file_seams_own_their_error_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv_builder, expected: str
) -> None:
    """A missing Wave C transport file must not surface as a Forge failure."""
    _config, _layout, _store, result = create_session_directly(tmp_path, r_item())
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    captured = io.StringIO()
    monkeypatch.setattr("sys.stderr", captured)

    assert cli.main(argv_builder(tmp_path, result.session_id)) == cli.EXIT_REFUSED
    message = captured.getvalue()
    assert "could not be read" in message
    assert "RuntimeForgeBridgeError" not in message


def test_semantic_export_transport_write_is_bridge_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vocab.runtime.errors import RuntimeSemanticBridgeError

    layout, store, result, _outcome = captured_attempt(
        tmp_path, r_item(), "subtle means hard to notice".encode("utf-8")
    )
    existing = tmp_path / "already-there.json"
    existing.write_bytes(b"do not overwrite")
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    with pytest.raises(RuntimeSemanticBridgeError):
        cli._command_semantic_export(
            cli.build_parser().parse_args(
                ["semantic-export", "--config", str(tmp_path / "runtime.json"),
                 "--session-id", result.session_id, "--item-ordinal", "0",
                 "--out", str(existing)]
            )
        )
    assert existing.read_bytes() == b"do not overwrite"


# ----------------------------------------------------------------------
# Production terminal I/O
# ----------------------------------------------------------------------


class FakeBuffer:
    def __init__(self, fail_on: str = "none") -> None:
        self.fail_on = fail_on
        self.written = b""

    def write(self, data: bytes) -> int:
        if self.fail_on == "buffer-write":
            raise OSError("broken pipe")
        self.written += data
        return len(data)

    def flush(self) -> None:
        if self.fail_on == "buffer-flush":
            raise OSError("broken pipe")


class FakeTextStream:
    """A text stream exposing a binary boundary, as a real terminal does."""

    def __init__(self, fail_on: str = "none", *, with_buffer: bool = True) -> None:
        self.fail_on = fail_on
        self.text = ""
        if with_buffer:
            self.buffer = FakeBuffer(fail_on)

    def write(self, data: str) -> int:
        if self.fail_on == "write":
            raise OSError("broken pipe")
        self.text += data
        return len(data)

    def flush(self) -> None:
        if self.fail_on == "flush":
            raise OSError("broken pipe")

    def readline(self) -> str:
        if self.fail_on == "readline":
            raise OSError("terminal closed")
        return "SKIP\n"


def test_terminal_writes_exact_bytes_to_the_binary_boundary() -> None:
    out = FakeTextStream()
    port = cli._TerminalAttemptPort(FakeTextStream(), out)
    payload = b"passage line\n\nquestion line"
    port.display_stimulus(payload)
    assert out.buffer.written == payload
    assert out.text == ""


def test_terminal_without_a_binary_stream_refuses(monkeypatch) -> None:
    """No text fallback: decoding could alter the verified artifact."""
    out = FakeTextStream(with_buffer=False)
    port = cli._TerminalAttemptPort(FakeTextStream(), out)
    with pytest.raises(RuntimeAttemptError, match="binary output stream"):
        port.display_stimulus(b"stimulus")
    assert out.text == ""


@pytest.mark.parametrize("fail_on", ("flush", "buffer-write", "buffer-flush"))
def test_terminal_display_oserror_is_a_refusal(fail_on: str) -> None:
    port = cli._TerminalAttemptPort(FakeTextStream(), FakeTextStream(fail_on))
    with pytest.raises(RuntimeAttemptError, match="could not be displayed"):
        port.display_stimulus(b"stimulus")


def test_terminal_readline_oserror_is_a_refusal() -> None:
    port = cli._TerminalAttemptPort(
        FakeTextStream("readline"), FakeTextStream()
    )
    with pytest.raises(RuntimeAttemptError, match="could not be collected"):
        port.ask_terminal_action()


def test_terminal_port_never_decodes_the_payload() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    start = source.index("class _TerminalAttemptPort:")
    end = source.index("\ndef ", start)
    body = source[start:end]
    assert "payload.decode" not in body
    assert ".decode(" not in body


def test_terminal_oserror_after_reservation_writes_no_receipt(
    tmp_path: Path
) -> None:
    _config, layout, store, result = create_session_directly(tmp_path, r_item())

    class FailingPort:
        def display_stimulus(self, payload: bytes) -> None:
            raise RuntimeAttemptError("stimulus could not be displayed")

        def ask_terminal_action(self) -> str:
            raise AssertionError("must not be reached")

    capture_before = layout.capture_path.read_bytes()
    disposition_before = layout.disposition_path.read_bytes()
    with pytest.raises(RuntimeAttemptError):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=FailingPort(),
        )
    assert layout.capture_path.read_bytes() == capture_before
    assert layout.disposition_path.read_bytes() == disposition_before


def test_injected_port_runtime_error_still_propagates(tmp_path: Path) -> None:
    """The abstract port is never wrapped in a broad catch."""
    _config, layout, store, result = create_session_directly(tmp_path, r_item())
    port = RecordingPort(attempt_runner.SKIP)
    port.fail_display = True
    with pytest.raises(RuntimeError, match="display device failed"):
        attempt_runner.run_fresh_attempt(
            layout,
            session_id=result.session_id,
            item_ordinal=0,
            artifact_store=store,
            port=port,
        )


def test_attempt_runner_does_not_wrap_the_abstract_port() -> None:
    source = Path("vocab/runtime/attempt_runner.py").read_text(encoding="utf-8")
    assert "port.display_stimulus(stimulus_bytes)" in source
    display_line = source.index("port.display_stimulus(stimulus_bytes)")
    window = source[max(0, display_line - 300):display_line]
    assert "with normalized(" not in window.rsplit("\n\n", 1)[-1]


# ----------------------------------------------------------------------
# SessionPlan revalidation at create_session
# ----------------------------------------------------------------------


def build_plan_item(**overrides):
    from vocab.runtime.session_plan import SessionPlanItem

    values = {
        "unit_key": R_UNIT,
        "channel": "R",
        "stimulus": {"passage": "p", "question": "q"},
    }
    values.update(overrides)
    return SessionPlanItem(**values)


@pytest.mark.parametrize(
    ("plan_items", "match"),
    (
        pytest.param((), "at least one item", id="empty-items"),
        pytest.param(
            (build_plan_item(channel="L", stimulus={"spoken_script": "s", "question": "q"}),),
            "exactly 'R' or 'W'",
            id="caller-built-L",
        ),
        pytest.param(
            (build_plan_item(stimulus={"passage": "p"}),),
            "must carry exactly",
            id="missing-stimulus-field",
        ),
        pytest.param(
            (build_plan_item(stimulus={"passage": "p", "question": "q", "extra": "x"}),),
            "must carry exactly",
            id="extra-stimulus-field",
        ),
        pytest.param(
            (build_plan_item(unit_key="not a key"),), "unit_key is invalid",
            id="bad-unit-key",
        ),
        pytest.param(
            (build_plan_item(stimulus={"passage": "   ", "question": "q"}),),
            "must not be blank",
            id="blank-stimulus",
        ),
    ),
)
def test_directly_constructed_plan_is_refused_before_any_write(
    tmp_path: Path, plan_items, match: str
) -> None:
    from vocab.runtime.session_plan import SessionPlan

    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    before = snapshot(layout.data_root)
    with pytest.raises(RuntimeSessionPlanError, match=match):
        assessment_session.create_session(
            SessionPlan(items=plan_items),
            anki=assessment_anki(),
            artifact_store=store,
            session_root=layout.session_root,
        )
    assert snapshot(layout.data_root) == before


def test_create_session_uses_a_detached_plan_snapshot(tmp_path: Path) -> None:
    """The validated snapshot is immune to later mutation of the caller's data.

    create_session validates at the moment it is called, so a mutation made
    before that call is legitimately seen. What must hold is that once a plan is
    validated, nothing the caller still holds can reach back into the snapshot
    that session derivation uses.
    """
    from vocab.runtime.session_plan import SessionPlan

    _config, layout = deployment(tmp_path)
    store = open_deployment_artifact_store(layout)
    mutable = {"passage": "original passage", "question": "original question"}
    plan = SessionPlan(items=(build_plan_item(stimulus=mutable),))

    result = assessment_session.create_session(
        plan,
        anki=assessment_anki(),
        artifact_store=store,
        session_root=layout.session_root,
    )
    mutable["passage"] = "swapped after the session was derived"

    decoded = strict_json_loads(
        load_session_manifest(layout.session_root, result.session_id).canonical_bytes
    )
    assert decoded["items"][0]["stimulus"]["passage"] == "original passage"

    validated = assessment_session.validate_session_plan(plan)
    snapshot_stimulus = validated.items[0].stimulus
    with pytest.raises(TypeError):
        snapshot_stimulus["passage"] = "cannot be written"  # type: ignore[index]

    mutable["question"] = "mutated again"
    assert snapshot_stimulus["question"] == "original question"


def test_parse_session_plan_passes_through_the_shared_validator() -> None:
    source = Path("vocab/runtime/session_plan.py").read_text(encoding="utf-8")
    assert source.count("def validate_session_plan(") == 1
    tail = source[source.index("def parse_session_plan("):]
    assert "return validate_session_plan(" in tail


# ----------------------------------------------------------------------
# No silent coercion of validated manifest values
# ----------------------------------------------------------------------


@pytest.mark.parametrize("relative", WAVE_C_SOURCES)
def test_no_str_coercion_of_manifest_values(relative: str) -> None:
    """A non-string persisted value must fail, never be converted."""
    import re

    source = Path(relative).read_text(encoding="utf-8")
    for pattern in (
        r'str\(\s*item\[',
        r'str\(\s*stimulus\[',
        r'str\(\s*manifest\.',
        r'str\(\s*decoded\[',
    ):
        assert re.search(pattern, source) is None, f"{relative} coerces via {pattern}"


# ----------------------------------------------------------------------
# F3: the plan parser owns malformed JSON only
# ----------------------------------------------------------------------


def test_f3_malformed_json_is_a_plan_refusal() -> None:
    with pytest.raises(RuntimeSessionPlanError, match="not strict JSON"):
        parse_session_plan(b"{not json")


def test_f3_artifact_json_error_is_a_plan_refusal(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    from vocab.artifact_json import ArtifactJSONError
    from vocab.runtime import session_plan

    def raising(raw):
        raise ArtifactJSONError("duplicate key")

    monkeypatch.setattr(session_plan, "strict_json_loads", raising)
    with pytest.raises(RuntimeSessionPlanError, match="not strict JSON"):
        parse_session_plan(plan_bytes(r_item()))


@pytest.mark.parametrize(
    "defect",
    (
        pytest.param(TypeError("decoder given the wrong type"), id="TypeError"),
        pytest.param(ValueError("synthetic bare defect"), id="bare-ValueError"),
    ),
)
def test_f3_decoder_defects_propagate(
    monkeypatch: pytest.MonkeyPatch, defect: BaseException
) -> None:
    """raw_bytes is already type-guarded, so these are defects, not input errors."""
    from vocab.runtime import session_plan

    def raising(raw):
        raise defect

    monkeypatch.setattr(session_plan, "strict_json_loads", raising)
    with pytest.raises(type(defect)):
        parse_session_plan(plan_bytes(r_item()))


# ----------------------------------------------------------------------
# F2: T12 history preflight excludes the transcription family
# ----------------------------------------------------------------------


def test_f2_transcription_error_from_t12_validation_is_a_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_t12_histories cannot raise it, so it must not be swallowed."""
    from vocab.runtime import preflight
    from vocab.transcription_ledger import TranscriptionLedgerError

    bootstrap_deployment(tmp_path, FakeAnki([]))
    plan_path = write_plan(tmp_path, r_item())

    def raising(**kwargs):
        raise TranscriptionLedgerError("synthetic, unreachable from this call")

    monkeypatch.setattr(preflight, "validate_t12_histories", raising)
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    with pytest.raises(TranscriptionLedgerError, match="synthetic"):
        cli.main(
            ["session-create", "--config", str(tmp_path / "runtime.json"),
             "--plan", str(plan_path)]
        )


def test_f2_disposition_error_from_t12_validation_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = bootstrap_deployment(tmp_path, FakeAnki([]))
    layout = build_layout(config.data_root)
    layout.disposition_path.write_bytes(b'{"torn": true}\n')
    plan_path = write_plan(tmp_path, r_item())
    monkeypatch.setattr(cli, "_build_anki", lambda cfg: assessment_anki())
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert cli.main(
        ["session-create", "--config", str(tmp_path / "runtime.json"),
         "--plan", str(plan_path)]
    ) == cli.EXIT_REFUSED


# ----------------------------------------------------------------------
# F1/F2/F3/F4 static check, scoped to the exact call sites
# ----------------------------------------------------------------------


def seam_at(relative: str, marker: str) -> str:
    """Return the catching= argument of the normalized() block for one call."""
    source = Path(relative).read_text(encoding="utf-8")
    index = source.index(marker)
    window = source[:index]
    block = window.rindex("with normalized(")
    return source[block:index]


@pytest.mark.parametrize(
    ("relative", "marker", "required", "forbidden"),
    (
        pytest.param(
            "vocab/runtime/semantic_bridge.py", "request = build_semantic_request(",
            "SEMANTIC_REQUEST_SEAM", ("SEMANTIC_BINDING_SEAM", "SEMANTIC_SEAM"),
            id="request-construction",
        ),
        pytest.param(
            "vocab/runtime/semantic_bridge.py", "imported = import_semantic_response(",
            "SEMANTIC_PROPOSAL_SEAM", ("HUMAN_REVIEW_SEAM", "SEMANTIC_BINDING_SEAM"),
            id="proposal-import",
        ),
        pytest.param(
            "vocab/runtime/semantic_bridge.py", "review = build_human_review(",
            "HUMAN_REVIEW_SEAM", ("SEMANTIC_PROPOSAL_SEAM", "SEMANTIC_BINDING_SEAM"),
            id="review-construction",
        ),
        pytest.param(
            "vocab/runtime/semantic_bridge.py", "bundle = bind_t11_semantic_evidence(",
            "SEMANTIC_BINDING_SEAM", (),
            id="binder-composite",
        ),
        pytest.param(
            "vocab/runtime/semantic_bridge.py", "appended = emit_planned_judge(",
            "PRODUCER_SEAM", ("PLANNING_SEAM",),
            id="producer",
        ),
        pytest.param(
            "vocab/runtime/attempt_runner.py", "permit = reserve_exposure(",
            "RESERVATION_SEAM", ("ATTEMPT_SEAM",),
            id="reservation",
        ),
        pytest.param(
            "vocab/runtime/attempt_runner.py", "receipt = close_text_submission(",
            "TERMINAL_CAPTURE_SEAM", ("RESERVATION_SEAM", "ATTEMPT_SEAM"),
            id="close-submission",
        ),
        pytest.param(
            "vocab/runtime/preflight.py", "validate_t12_histories(",
            "T12_HISTORY_SEAM", ("TRANSCRIPTION_SEAM", "LEDGER_SEAM"),
            id="t12-history-preflight",
        ),
        pytest.param(
            "vocab/runtime/preflight.py", "records = read_transcription_ledger(",
            "TRANSCRIPTION_SEAM", ("T12_HISTORY_SEAM", "LEDGER_SEAM"),
            id="transcription-preflight",
        ),
    ),
)
def test_seam_scoped_to_its_exact_call(
    relative: str, marker: str, required: str, forbidden: tuple[str, ...]
) -> None:
    block = seam_at(relative, marker)
    assert required in block, f"{marker} should use {required}"
    for name in forbidden:
        assert name not in block, f"{marker} must not use {name}"


def test_seam_membership_matches_reachability() -> None:
    from vocab.assessment_evidence import AssessmentEvidenceError
    from vocab.runtime import normalize as n
    from vocab.session import SessionManifestError
    from vocab.transcription_ledger import TranscriptionLedgerError

    assert TranscriptionLedgerError not in n.T12_HISTORY_SEAM
    assert TranscriptionLedgerError not in n.TEXT_EVIDENCE_SEAM
    assert SessionManifestError not in n.TERMINAL_CAPTURE_SEAM
    assert SessionManifestError in n.RESERVATION_SEAM
    assert AssessmentEvidenceError not in n.PRODUCER_SEAM
    assert AssessmentEvidenceError in n.TEXT_EVIDENCE_SEAM
    assert AssessmentEvidenceError in n.SEMANTIC_BINDING_SEAM


def test_parse_session_plan_does_not_catch_type_error() -> None:
    source = Path("vocab/runtime/session_plan.py").read_text(encoding="utf-8")
    tail = source[source.index("def parse_session_plan("):]
    assert "except ArtifactJSONError as exc:" in tail
    assert "TypeError" not in tail.split("def ", 1)[0]
