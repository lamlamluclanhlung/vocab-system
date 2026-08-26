"""D68 producer preflight, idempotency, crash recovery, and AST authority."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import vocab.assessment_producer as producer_module
import vocab.events as events_module
import vocab.exposure as exposure_module
from tests.t12_ast_invariants import (
    APPROVED_EVENT_LOG_AUTHORITIES,
    assert_t12_ast_invariants,
)
from tests.test_t12_assessment_planning import (
    append_attempt,
    bind,
    make_unit as make_text_unit,
    planned as planned_text,
)
from tests.test_t12_disposition import (
    make_runtime as make_disposition_runtime,
    make_unit as make_disposition_unit,
    record_disposition,
)
from tests.test_t12_speech_planning import planned_success
from vocab.assessment_evidence import validate_unit_evidence
from vocab.assessment_planning import plan_policy_judge, plan_text_judge
from vocab.assessment_producer import (
    AssessmentProducerAppendError,
    AssessmentProducerError,
    AssessmentProducerHistoryError,
    emit_planned_judge,
    emit_planned_speech_assessment,
)
from vocab.disposition_ledger import DISPOSITION_CODES
from vocab.events import EventLog


def _runtime_arguments(runtime: object, event_log: EventLog) -> dict[str, object]:
    return {
        "event_log": event_log,
        "exposure_path": runtime.exposure_path,
        "capture_path": runtime.capture_path,
        "disposition_path": runtime.disposition_path,
        "artifact_store": runtime.store,
    }


def test_static_d68_eventlog_authority_is_exact() -> None:
    assert len(APPROVED_EVENT_LOG_AUTHORITIES) == 7
    assert_t12_ast_invariants(Path(__file__).resolve().parents[1])


@pytest.mark.parametrize(
    ("relative_path", "old", "new", "match"),
    (
        (
            "vocab/assessment_producer.py",
            'event_log.log("JUDGE", unit_key, payload)',
            'event_log.log(event="JUDGE", unit_key=unit_key, payload=payload)',
            "unapproved",
        ),
        (
            "vocab/assessment_producer.py",
            'stored = event_log.log("JUDGE", unit_key, payload)',
            'logger = event_log.log\n        stored = logger("JUDGE", unit_key, payload)',
            "captured",
        ),
        (
            "vocab/assessment_producer.py",
            "def _entry_gate(",
            'def _forbidden_capture(value):\n    return getattr(value, "log")\n\n\ndef _entry_gate(',
            "getattr",
        ),
        (
            "vocab/artifact_store.py",
            "from __future__ import annotations",
            "from __future__ import annotations\n\nfrom vocab.events import EventLog",
            "concrete EventLog import",
        ),
        (
            "vocab/reconcile.py",
            'event_log.log("STATE", unit_key, payload)',
            'event_log.log("JUDGE", unit_key, payload)',
            "unapproved",
        ),
    ),
)
def test_ast_invariant_rejects_authority_mutations(
    tmp_path: Path,
    relative_path: str,
    old: str,
    new: str,
    match: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / relative_path
    source = target.read_text(encoding="utf-8")
    assert old in source
    target.write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(AssertionError, match=match):
        assert_t12_ast_invariants(tmp_path)


@pytest.mark.parametrize(
    ("scope_source", "expected_scope", "remove_approved"),
    (
        (
            """
def outer():
    def emit_scan(event_log):
        return event_log.log("ENCOUNTER", "unit", {})
""",
            "function:outer.function:emit_scan",
            False,
        ),
        (
            """
class SomeClass:
    def emit_scan(self, event_log):
        return event_log.log("ENCOUNTER", "unit", {})
""",
            "class:SomeClass.function:emit_scan",
            False,
        ),
        (
            """
def async_wrapper():
    async def emit_scan(event_log):
        return event_log.log("ENCOUNTER", "unit", {})
""",
            "function:async_wrapper.async-function:emit_scan",
            False,
        ),
        (
            """
def moved_outer():
    def emit_scan(event_log):
        return event_log.log("ENCOUNTER", "unit", {})
""",
            "function:moved_outer.function:emit_scan",
            True,
        ),
        (
            """
class MovedClass:
    def emit_scan(self, event_log):
        return event_log.log("ENCOUNTER", "unit", {})
""",
            "class:MovedClass.function:emit_scan",
            True,
        ),
        (
            '\nevent_log.log("ENCOUNTER", "unit", {})\n',
            "<module>",
            False,
        ),
    ),
)
def test_ast_invariant_uses_full_lexical_scope(
    tmp_path: Path,
    scope_source: str,
    expected_scope: str,
    remove_approved: bool,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / "vocab/corpus.py"
    source = target.read_text(encoding="utf-8")
    if remove_approved:
        approved_call = """stored = event_log.log(
                "ENCOUNTER",
                plan.unit_key,
                exact_payload_copy,
            )"""
        assert approved_call in source
        source = source.replace(approved_call, "stored = None", 1)
    target.write_text(source + scope_source, encoding="utf-8")

    with pytest.raises(AssertionError) as raised:
        assert_t12_ast_invariants(tmp_path)
    assert expected_scope in str(raised.value)
    if remove_approved:
        assert "approved EventLog authority count is 0" in str(raised.value)


@pytest.mark.parametrize(
    "replacement",
    (
        'event_type = "JUDGE"\n        stored = event_log.log(event_type, unit_key, payload)',
        "stored = event_log.log(T12_ASSESSMENT_PRODUCER_ID, unit_key, payload)",
        'stored = event_log.log(f"JUDGE", unit_key, payload)',
        'stored = event_log.log("JUD" + "GE", unit_key, payload)',
        'stored = event_log.log(event="JUDGE", unit_key=unit_key, payload=payload)',
        "stored = event_log.log()",
        'stored = event_log.log("STATE", unit_key, payload)',
        'stored = event_log.log("JUDGE", unit_key, payload)\n        event_log.log("JUDGE", unit_key, payload)',
    ),
)
def test_ast_invariant_rejects_nonliteral_wrong_and_duplicate_authority(
    tmp_path: Path,
    replacement: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / "vocab/assessment_producer.py"
    source = target.read_text(encoding="utf-8")
    old = 'stored = event_log.log("JUDGE", unit_key, payload)'
    assert old in source
    target.write_text(source.replace(old, replacement, 1), encoding="utf-8")

    with pytest.raises(AssertionError, match="authority"):
        assert_t12_ast_invariants(tmp_path)


@pytest.mark.parametrize(
    "replacement",
    (
        "writer = event_log.log\n        stored = writer",
        "writers = [event_log.log]\n        stored = writers",
        "stored = (event_log.log,)",
        "stored = identity(event_log.log)",
        "stored = partial(event_log.log)",
    ),
)
def test_ast_invariant_rejects_log_capture_in_all_expression_contexts(
    tmp_path: Path,
    replacement: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / "vocab/assessment_producer.py"
    source = target.read_text(encoding="utf-8")
    old = 'stored = event_log.log("JUDGE", unit_key, payload)'
    assert old in source
    target.write_text(source.replace(old, replacement, 1), encoding="utf-8")

    with pytest.raises(AssertionError, match="captured .log authority"):
        assert_t12_ast_invariants(tmp_path)


@pytest.mark.parametrize(
    "snippet",
    (
        "_lookup = getattr\nwriter = _lookup(event_log, 'log')",
        "functions = (getattr, [getattr])",
        "def return_getattr():\n    return getattr",
        "def pass_getattr(function):\n    return function(getattr)",
        "from builtins import getattr as lookup",
        "from builtins import *",
        "import builtins\n_lookup = builtins.getattr",
        "writer = getattr(event_log, 'log')",
    ),
)
def test_ast_invariant_rejects_every_producer_getattr_path(
    tmp_path: Path,
    snippet: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / "vocab/assessment_producer.py"
    source = target.read_text(encoding="utf-8")
    marker = "def _entry_gate("
    assert marker in source
    target.write_text(
        source.replace(marker, snippet + "\n\n\n" + marker, 1),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="producer getattr"):
        assert_t12_ast_invariants(tmp_path)


@pytest.mark.parametrize(
    "statement",
    (
        "from .events import EventLog",
        "from .events import EventLog as ConcreteLog",
        "from .events import *",
        "from vocab.events import EventLog",
        "from vocab.events import *",
        "import vocab.events",
        "import vocab.events as concrete_events",
        "from . import events",
        "from vocab import events",
    ),
)
def test_ast_invariant_rejects_every_concrete_eventlog_import_form(
    tmp_path: Path,
    statement: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    target = tmp_path / "vocab/artifact_store.py"
    source = target.read_text(encoding="utf-8")
    marker = "from __future__ import annotations"
    assert marker in source
    target.write_text(
        source.replace(marker, marker + "\n\n" + statement, 1),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="concrete EventLog import"):
        assert_t12_ast_invariants(tmp_path)


def test_text_missing_then_exact_rerun(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")

    first = emit_planned_judge(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )
    second = emit_planned_judge(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )

    assert len(first) == 1
    assert first[0].event == "JUDGE"
    assert second == ()
    assert log.read() == [first[0]]


def test_text_conflicting_slot_fails_closed(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    payload = plan.to_payload()
    payload["model_version"] = "conflicting-version"
    payload["provenance"]["semantic_judge"]["assessor_version"] = (
        "conflicting-version"
    )
    log.log("JUDGE", plan.unit_key, payload)

    with pytest.raises(AssessmentProducerHistoryError, match="conflict"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert len(log.read()) == 1


def test_duplicate_text_slot_fails_even_when_identical(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.log("JUDGE", plan.unit_key, plan.to_payload())
    log.log("JUDGE", plan.unit_key, plan.to_payload())

    with pytest.raises(AssessmentProducerHistoryError, match="duplicate"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


@pytest.mark.parametrize(
    "raw",
    (
        b'{"unfinished":',
        b"\xff\n",
        b'{"not":"an event"}\n',
    ),
)
def test_malformed_final_eventlog_fails_closed(
    tmp_path: Path,
    raw: bytes,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    path.write_bytes(raw)

    with pytest.raises(AssessmentProducerHistoryError):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_malformed_interior_eventlog_fails_closed(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    path.write_bytes(b"not-json\n{}\n")

    with pytest.raises(AssessmentProducerHistoryError):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_valid_final_record_without_newline_is_a_torn_append(
    tmp_path: Path,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.log("JUDGE", plan.unit_key, plan.to_payload())
    path.write_bytes(path.read_bytes().removesuffix(b"\n"))

    with pytest.raises(AssessmentProducerHistoryError, match="newline"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_eventlog_subclass_and_duck_type_are_rejected_before_preflight(
    tmp_path: Path,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)

    class SubLog(EventLog):
        pass

    class DuckLog:
        path = tmp_path / "duck.jsonl"

        def read(self):
            raise AssertionError("must not read")

        def log(self, *_args):
            raise AssertionError("must not append")

    for candidate in (SubLog(tmp_path / "sub.jsonl"), DuckLog()):
        with pytest.raises(TypeError, match="exactly an EventLog"):
            emit_planned_judge(
                **_runtime_arguments(runtime, candidate),
                planned=plan,
            )


def test_plan_from_unrelated_durable_history_is_rejected(tmp_path: Path) -> None:
    plan, _first, *_ = planned_text(tmp_path / "first")
    _other_plan, second, *_ = planned_text(tmp_path / "second")
    log = EventLog(tmp_path / "events.jsonl")

    with pytest.raises(AssessmentProducerHistoryError, match="exposure"):
        emit_planned_judge(
            **_runtime_arguments(second, log),
            planned=plan,
        )
    assert log.read() == []


@pytest.mark.parametrize("entry_point", ("judge", "speech"))
def test_each_public_invocation_validates_triple_history_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_point: str,
) -> None:
    if entry_point == "judge":
        plan, runtime, *_ = planned_text(tmp_path)
    else:
        plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    calls = {"validate": 0, "exposure": 0, "capture": 0, "disposition": 0}
    original_validate = producer_module.validate_t12_histories
    original_readers = {
        "exposure": exposure_module.read_exposure_ledger,
        "capture": exposure_module.read_capture_ledger,
        "disposition": exposure_module.read_disposition_ledger,
    }

    def counted_validate(**kwargs):
        calls["validate"] += 1
        return original_validate(**kwargs)

    def counted_reader(name):
        original = original_readers[name]

        def read(path):
            calls[name] += 1
            return original(path)

        return read

    monkeypatch.setattr(
        producer_module,
        "validate_t12_histories",
        counted_validate,
    )
    for name in ("exposure", "capture", "disposition"):
        monkeypatch.setattr(
            exposure_module,
            f"read_{name}_ledger",
            counted_reader(name),
        )

    if entry_point == "judge":
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    else:
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert calls == {"validate": 1, "exposure": 1, "capture": 1, "disposition": 1}


@pytest.mark.parametrize("first_attempt", (True, False))
def test_historical_false_novelty_fails_in_both_directions(
    tmp_path: Path,
    first_attempt: bool,
) -> None:
    first_plan, runtime, *_ = planned_text(tmp_path)
    plan = first_plan
    if not first_attempt:
        runtime = append_attempt(runtime)
        attempt = runtime.evidence()
        unit = validate_unit_evidence(make_text_unit("R"))
        semantic, presence, _ = bind(attempt, unit)
        plan = plan_text_judge(
            attempt=attempt,
            unit=unit,
            semantic=semantic,
            presence=presence,
        )
    payload = plan.to_payload()
    payload["novel"] = not payload["novel"]
    log = EventLog(tmp_path / "events.jsonl")
    log.log("JUDGE", plan.unit_key, payload)

    with pytest.raises(AssessmentProducerHistoryError, match="D35"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_historical_response_ref_must_match_capture(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    payload = plan.to_payload()
    payload["response_artifact_ref"] = "sha256:" + "0" * 64
    log = EventLog(tmp_path / "events.jsonl")
    log.log("JUDGE", plan.unit_key, payload)

    with pytest.raises(AssessmentProducerHistoryError, match="capture"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


@pytest.mark.parametrize("code", sorted(DISPOSITION_CODES))
def test_all_policy_dispositions_emit_idempotently(
    tmp_path: Path,
    code: str,
) -> None:
    runtime, permit = make_disposition_runtime(tmp_path, "R")
    record_disposition(runtime, permit, code)
    plan = plan_policy_judge(
        disposition=runtime.disposition_evidence(),
        unit=validate_unit_evidence(make_disposition_unit("R")),
    )
    log = EventLog(tmp_path / "events.jsonl")

    assert len(
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    ) == 1
    assert emit_planned_judge(
        **_runtime_arguments(runtime, log),
        planned=plan,
    ) == ()


def test_speech_missing_pair_then_exact_rerun(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")

    first = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )
    second = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )

    assert [item.event for item in first] == ["SPEAK", "JUDGE"]
    assert second == ()
    assert [item.event for item in log.read()] == ["SPEAK", "JUDGE"]


def test_speech_exact_speak_missing_judge_resumes(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    speak = log.log("SPEAK", plan.unit_key, plan.speak_payload())

    appended = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )

    assert [item.event for item in appended] == ["JUDGE"]
    assert log.read()[0] == speak


def test_speech_judge_without_speak_is_never_repaired(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.log("JUDGE", plan.unit_key, plan.judge_payload())

    with pytest.raises(AssessmentProducerHistoryError, match="without|no companion"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert [item.event for item in log.read()] == ["JUDGE"]


@pytest.mark.parametrize("event_type", ("SPEAK", "JUDGE"))
def test_duplicate_speech_slot_fails_closed(
    tmp_path: Path,
    event_type: str,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    if event_type == "JUDGE":
        log.log("SPEAK", plan.unit_key, plan.speak_payload())
        payload = plan.judge_payload()
    else:
        payload = plan.speak_payload()
    log.log(event_type, plan.unit_key, payload)
    log.log(event_type, plan.unit_key, payload)

    with pytest.raises(AssessmentProducerHistoryError, match="duplicate"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_conflicting_speak_slot_fails_closed(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    payload = plan.speak_payload()
    payload["model_version"] = "conflicting-version"
    payload["provenance"]["semantic_judge"]["assessor_version"] = (
        "conflicting-version"
    )
    log = EventLog(tmp_path / "events.jsonl")
    log.log("SPEAK", plan.unit_key, payload)

    with pytest.raises(AssessmentProducerHistoryError, match="conflict"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_conflicting_speech_judge_fails_closed_without_append(
    tmp_path: Path,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    judge = plan.judge_payload()
    judge["model_version"] = "conflicting-version"
    judge["provenance"]["semantic_judge"]["assessor_version"] = (
        "conflicting-version"
    )
    log = EventLog(tmp_path / "events.jsonl")
    log.log("SPEAK", plan.unit_key, plan.speak_payload())
    log.log("JUDGE", plan.unit_key, judge)
    before = log.path.read_bytes()

    with pytest.raises(AssessmentProducerHistoryError, match="conflict"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert log.path.read_bytes() == before


def test_sealed_speech_plan_rejects_unrelated_durable_history(
    tmp_path: Path,
) -> None:
    plan, _first_runtime, *_ = planned_success(tmp_path / "first")
    _other_plan, second_runtime, *_ = planned_success(tmp_path / "second")
    log = EventLog(tmp_path / "events.jsonl")

    with pytest.raises(AssessmentProducerHistoryError, match="exposure"):
        emit_planned_speech_assessment(
            **_runtime_arguments(second_runtime, log),
            planned=plan,
        )
    assert log.read() == []


def test_corrupt_unrelated_t12_attempt_blocks_current_emission(
    tmp_path: Path,
) -> None:
    first_plan, runtime, *_ = planned_text(tmp_path)
    runtime = append_attempt(runtime)
    current_attempt = runtime.evidence()
    unit = validate_unit_evidence(make_text_unit("R"))
    semantic, presence, _ = bind(current_attempt, unit)
    current_plan = plan_text_judge(
        attempt=current_attempt,
        unit=unit,
        semantic=semantic,
        presence=presence,
    )
    corrupt = first_plan.to_payload()
    corrupt["response_artifact_ref"] = "sha256:" + "0" * 64
    log = EventLog(tmp_path / "events.jsonl")
    log.log("JUDGE", first_plan.unit_key, corrupt)
    before = log.path.read_bytes()

    with pytest.raises(AssessmentProducerHistoryError, match="capture"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=current_plan,
        )
    assert log.path.read_bytes() == before


def test_generic_speech_noise_does_not_count_as_t12_state(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.log(
        "SPEAK",
        plan.unit_key,
        {
            "audio_path": "generic.wav",
            "transcript": "generic",
            "passed": False,
            "model_id": "generic",
            "model_version": "1",
            "attempt_id": plan.attempt_id,
        },
    )
    log.log(
        "JUDGE",
        plan.unit_key,
        {
            "channel": "S",
            "passed": False,
            "model_id": "generic",
            "model_version": "1",
            "attempt_id": plan.attempt_id,
        },
    )

    appended = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )
    assert [item.event for item in appended] == ["SPEAK", "JUDGE"]


def test_speak_append_failure_never_attempts_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    calls: list[str] = []

    def fail(self, event, unit_key, payload):
        calls.append(event)
        raise OSError("simulated append failure")

    monkeypatch.setattr(events_module.EventLog, "log", fail)
    with pytest.raises(AssessmentProducerAppendError, match="SPEAK"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert calls == ["SPEAK"]


def test_speak_may_be_durable_when_append_raises_but_judge_is_not_attempted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    original = events_module.EventLog.log
    calls: list[str] = []

    def append_then_fail(self, event, unit_key, payload):
        calls.append(event)
        stored = original(self, event, unit_key, payload)
        if event == "SPEAK":
            raise OSError("return path failed after durable append")
        return stored

    monkeypatch.setattr(events_module.EventLog, "log", append_then_fail)
    with pytest.raises(AssessmentProducerAppendError, match="SPEAK"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert calls == ["SPEAK"]
    assert [item.event for item in EventLog(log.path).read()] == ["SPEAK"]


def test_judge_append_failure_after_speak_resumes_with_judge_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    original = events_module.EventLog.log
    calls: list[str] = []

    def fail_judge(self, event, unit_key, payload):
        calls.append(event)
        if event == "JUDGE":
            raise OSError("simulated JUDGE append failure")
        return original(self, event, unit_key, payload)

    monkeypatch.setattr(events_module.EventLog, "log", fail_judge)
    with pytest.raises(AssessmentProducerAppendError, match="JUDGE"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert calls == ["SPEAK", "JUDGE"]
    assert [item.event for item in EventLog(log.path).read()] == ["SPEAK"]

    monkeypatch.setattr(events_module.EventLog, "log", original)
    resumed = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, log),
        planned=plan,
    )
    assert [item.event for item in resumed] == ["JUDGE"]
    assert [item.event for item in log.read()] == ["SPEAK", "JUDGE"]


def test_failed_post_speak_confirmation_never_appends_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    original = events_module.EventLog.read
    reads = 0

    def fail_confirmation(self, *args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            raise OSError("confirmation unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(events_module.EventLog, "read", fail_confirmation)
    with pytest.raises(AssessmentProducerHistoryError, match="strict"):
        emit_planned_speech_assessment(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert [item.event for item in EventLog(log.path).read()] == ["SPEAK"]


def test_text_append_failure_is_typed_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    calls = 0

    def fail(self, event, unit_key, payload):
        nonlocal calls
        calls += 1
        raise OSError("simulated append failure")

    monkeypatch.setattr(events_module.EventLog, "log", fail)
    with pytest.raises(AssessmentProducerError, match="JUDGE"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert calls == 1


def test_t12_v2_history_is_rejected_before_payload_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    stored = log.log("JUDGE", plan.unit_key, plan.to_payload())
    record = stored.to_dict()
    record["v"] = 2
    log.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    original = events_module._EVENT_DECODERS[1]
    monkeypatch.setitem(events_module._EVENT_DECODERS, 2, original)
    with pytest.raises(AssessmentProducerHistoryError, match="version"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )


def test_repository_event_schema_drift_fails_at_entry_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    monkeypatch.setattr(producer_module, "EVENT_SCHEMA_VERSION", 2)

    with pytest.raises(AssessmentProducerError, match="schema authority"):
        emit_planned_judge(
            **_runtime_arguments(runtime, log),
            planned=plan,
        )
    assert log.read() == []
