"""D69 real-filesystem smoke across T12 production and T9 consumption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_reconcile_observation import (
    FakeAnki,
    NOTE_ID,
    card_record,
    default_anki,
    make_unit as make_reconcile_unit,
)
from tests.test_t12_assessment_planning import planned as planned_text
from tests.test_t12_disposition import (
    DISPOSED_AT,
    make_runtime as make_disposition_runtime,
    make_unit as make_disposition_unit,
)
from tests.test_t12_speech_planning import planned_success
from vocab.assessment_evidence import validate_unit_evidence
from vocab.assessment_planning import plan_policy_judge
from vocab.assessment_producer import (
    emit_planned_judge,
    emit_planned_speech_assessment,
)
from vocab.disposition_ledger import (
    DISPOSITION_CODES,
    append_disposition_record,
    build_disposition_receipt,
)
from vocab.events import EventLog
from vocab.reconcile import decide_transitions, observe_unit


def _runtime_arguments(runtime: object, event_log: EventLog) -> dict[str, object]:
    return {
        "event_log": event_log,
        "exposure_path": runtime.exposure_path,
        "capture_path": runtime.capture_path,
        "disposition_path": runtime.disposition_path,
        "artifact_store": runtime.store,
    }


def _observation_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=1)


def _speech_anki() -> FakeAnki:
    return FakeAnki(
        make_reconcile_unit(states={"S": "NEW"}),
        [card_record(104, 3)],
        revlog={"104": []},
    )


def test_s1_text_pass_is_lifecycle_bearing(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_text(tmp_path)
    event_log = EventLog(tmp_path / "events.jsonl")

    appended = emit_planned_judge(
        **_runtime_arguments(runtime, event_log),
        planned=plan,
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=event_log,
        now=_observation_now(),
    )

    assert [event.event for event in appended] == ["JUDGE"]
    assert len(progress.channels[0].assessments) == 1
    assessment = progress.channels[0].assessments[0]
    assert assessment.passed is True
    assert assessment.novel is True


@pytest.mark.parametrize("disposition_code", sorted(DISPOSITION_CODES))
def test_s2_policy_abstain_is_lifecycle_inert(
    tmp_path: Path,
    disposition_code: str,
) -> None:
    runtime, permit = make_disposition_runtime(tmp_path, "R")
    permit.consume()
    append_disposition_record(
        runtime.disposition_path,
        build_disposition_receipt(
            disposed_at=DISPOSED_AT,
            attempt_id=runtime.attempt_id,
            disposition_code=disposition_code,
        ),
    )
    plan = plan_policy_judge(
        disposition=runtime.disposition_evidence(),
        unit=validate_unit_evidence(make_disposition_unit("R")),
    )
    event_log = EventLog(tmp_path / "events.jsonl")

    appended = emit_planned_judge(
        **_runtime_arguments(runtime, event_log),
        planned=plan,
    )
    progress = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=event_log,
        now=_observation_now(),
    )

    assert [event.event for event in appended] == ["JUDGE"]
    assert appended[0].payload["outcome"] == "ABSTAIN"
    assert progress.channels[0].assessments == ()


def test_s3_speech_pair_has_one_lifecycle_bearing_judge(tmp_path: Path) -> None:
    plan, runtime, *_ = planned_success(tmp_path)
    event_log = EventLog(tmp_path / "events.jsonl")

    appended = emit_planned_speech_assessment(
        **_runtime_arguments(runtime, event_log),
        planned=plan,
    )
    progress = observe_unit(
        NOTE_ID,
        anki=_speech_anki(),
        event_log=event_log,
        now=_observation_now(),
    )

    assert [event.event for event in appended] == ["SPEAK", "JUDGE"]
    assert len(progress.channels[0].assessments) == 1
    assert progress.channels[0].assessments[0].passed is True


def test_s4_exact_reruns_append_no_bytes_and_preserve_decision(
    tmp_path: Path,
) -> None:
    text_plan, text_runtime, *_ = planned_text(tmp_path / "text")
    text_log = EventLog(tmp_path / "text-events.jsonl")
    emit_planned_judge(
        **_runtime_arguments(text_runtime, text_log),
        planned=text_plan,
    )
    text_now = _observation_now()
    text_before = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=text_log,
        now=text_now,
    )
    text_decision = decide_transitions(text_before, now=text_now)
    text_bytes = text_log.path.read_bytes()
    assert emit_planned_judge(
        **_runtime_arguments(text_runtime, text_log),
        planned=text_plan,
    ) == ()
    assert text_log.path.read_bytes() == text_bytes
    text_after = observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=text_log,
        now=text_now,
    )
    assert decide_transitions(text_after, now=text_now) == text_decision

    speech_plan, speech_runtime, *_ = planned_success(tmp_path / "speech")
    speech_log = EventLog(tmp_path / "speech-events.jsonl")
    emit_planned_speech_assessment(
        **_runtime_arguments(speech_runtime, speech_log),
        planned=speech_plan,
    )
    speech_now = _observation_now()
    speech_before = observe_unit(
        NOTE_ID,
        anki=_speech_anki(),
        event_log=speech_log,
        now=speech_now,
    )
    speech_decision = decide_transitions(speech_before, now=speech_now)
    speech_bytes = speech_log.path.read_bytes()
    assert emit_planned_speech_assessment(
        **_runtime_arguments(speech_runtime, speech_log),
        planned=speech_plan,
    ) == ()
    assert speech_log.path.read_bytes() == speech_bytes
    speech_after = observe_unit(
        NOTE_ID,
        anki=_speech_anki(),
        event_log=speech_log,
        now=speech_now,
    )
    assert decide_transitions(speech_after, now=speech_now) == speech_decision
