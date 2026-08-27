"""Comprehensive tests for the pure, one-step T9.2 decision layer."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from vocab.contracts import (
    ANKI_LEECH_THRESHOLD,
    CHANNELS,
    EVENT_SCHEMA_VERSION,
    LIFECYCLE_SECONDS_PER_DAY,
    MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS,
    MASTERED_TO_DORMANT_DAYS,
    STABLE_MIN_AGE_DAYS,
    STABLE_MIN_INTERVAL_DAYS,
    STABLE_ZERO_LAPSE_WINDOW_DAYS,
    T9_DORMANCY_GROUP_KIND,
)
from vocab.models import ChannelProgress, LifecycleAssessment, UnitProgress
from vocab.reconcile import (
    ReconcileDecisionError,
    decide_transitions,
)


NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
UNIT_KEY = "subtle::small-difference"
DAY = timedelta(seconds=LIFECYCLE_SECONDS_PER_DAY)


def epoch_ms(instant: datetime) -> int:
    return int(instant.timestamp() * 1000)


def channel_progress(
    channel: str = "R",
    state: str = "NEW",
    **overrides: object,
) -> ChannelProgress:
    default_entry = "" if state == "NEW" else (NOW - 60 * DAY).isoformat()
    values: dict[str, object] = {
        "channel": channel,
        "state": state,
        "card_id": {"R": 101, "L": 102, "W": 103, "S": 104}.get(
            channel,
            999,
        ),
        "template_name": channel,
        "template_ordinal": {"R": 0, "L": 1, "W": 2, "S": 3}.get(
            channel,
            99,
        ),
        "interval_days": 0,
        "lapses_total": 0,
        "lapses_last_30_days": 0,
        "age_days": 0,
        "is_suspended": False,
        "state_episode_id": f"episode:{channel}:{state}",
        "state_entered_at": default_entry,
    }
    values.update(overrides)
    return ChannelProgress(**values)  # type: ignore[arg-type]


def assessment(
    assessment_id: str,
    instant: datetime,
    *,
    channel: str = "R",
    passed: bool = True,
    novel: bool = True,
    stimulus_ref: str | None = None,
) -> LifecycleAssessment:
    return LifecycleAssessment(
        channel=channel,
        passed=passed,
        assessment_id=assessment_id,
        stimulus_ref=(
            f"stimulus:{assessment_id}" if stimulus_ref is None else stimulus_ref
        ),
        novel=novel,
        ts=instant.isoformat(),
        model_id="human",
        model_version="1",
    )


def unit_progress(
    *channels: ChannelProgress,
    all_mastered_at: str = "",
    has_leech_tag: bool = False,
    unit_key: str = UNIT_KEY,
) -> UnitProgress:
    return UnitProgress(
        unit_key=unit_key,
        channels=tuple(channels),
        all_active_channels_mastered_at=all_mastered_at,
        has_leech_tag=has_leech_tag,
    )


def canonical_digest(identity: dict[str, object]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def expected_transition_id(transition: object) -> str:
    return canonical_digest(
        {
            "v": EVENT_SCHEMA_VERSION,
            "unit_key": UNIT_KEY,
            "channel": transition.channel,
            "from": transition.from_state,
            "to": transition.to_state,
            "trigger": transition.trigger,
            "from_episode_id": transition.from_episode_id,
            "evidence": transition.evidence,
        }
    )


def stable_learning_channel(**overrides: object) -> ChannelProgress:
    first = NOW - 60 * DAY
    values: dict[str, object] = {
        "state_entered_at": (NOW - 59 * DAY).isoformat(),
        "interval_days": STABLE_MIN_INTERVAL_DAYS,
        "age_days": STABLE_MIN_AGE_DAYS,
        "lapses_last_30_days": 0,
        "is_suspended": False,
        "first_lifecycle_review_id": epoch_ms(first),
        "latest_lifecycle_review_id": epoch_ms(NOW - DAY),
    }
    values.update(overrides)
    return channel_progress("R", "LEARNING", **values)


def stable_channel_with_assessments(
    *items: LifecycleAssessment,
    channel: str = "R",
    state: str = "STABLE",
    state_entry: datetime | None = None,
    **overrides: object,
) -> ChannelProgress:
    entry = NOW - 30 * DAY if state_entry is None else state_entry
    values: dict[str, object] = {
        "state_entered_at": entry.isoformat(),
        "assessments": tuple(items),
    }
    values.update(overrides)
    return channel_progress(channel, state, **values)


@pytest.mark.parametrize(
    "progress",
    [
        unit_progress(channel_progress(), unit_key="not-a-unit-key"),
        unit_progress(channel_progress(), channel_progress()),
        unit_progress(channel_progress("X")),
        unit_progress(channel_progress(state="UNKNOWN")),
        unit_progress(channel_progress(card_id=True)),
        unit_progress(channel_progress(state_episode_id="")),
        unit_progress(channel_progress(state_entered_at=NOW.isoformat())),
        unit_progress(channel_progress("R", "LEARNING", state_entered_at="")),
        unit_progress(
            channel_progress(
                "R",
                "LEARNING",
                state_entered_at=(NOW - 60 * DAY)
                .astimezone(timezone(timedelta(hours=7)))
                .isoformat(),
            )
        ),
        unit_progress(
            channel_progress(
                "R",
                "LEARNING",
                state_entered_at=(NOW + timedelta(milliseconds=1)).isoformat(),
            )
        ),
    ],
    ids=[
        "unit-key",
        "duplicate-channel",
        "unknown-channel",
        "unknown-state",
        "bool-card-id",
        "missing-episode",
        "new-entry-time",
        "missing-non-new-entry-time",
        "non-normalized-entry-time",
        "future-entry-time",
    ],
)
def test_structurally_impossible_progress_fails_closed(
    progress: UnitProgress,
) -> None:
    with pytest.raises(ReconcileDecisionError):
        decide_transitions(progress, now=NOW)


def test_cross_channel_assessment_fails_closed() -> None:
    item = assessment("a1", NOW - DAY, channel="L")
    progress = unit_progress(
        stable_channel_with_assessments(item, channel="R")
    )

    with pytest.raises(ReconcileDecisionError, match="another channel"):
        decide_transitions(progress, now=NOW)


@pytest.mark.parametrize(
    "bad_now",
    [datetime(2026, 8, 22, 12), "2026-08-22T12:00:00+00:00"],
    ids=["naive", "not-datetime"],
)
def test_explicit_now_must_be_an_aware_datetime(bad_now: object) -> None:
    with pytest.raises(ReconcileDecisionError, match="now"):
        decide_transitions(unit_progress(channel_progress()), now=bad_now)  # type: ignore[arg-type]


def test_aware_now_is_normalized_to_utc_internally() -> None:
    progress = unit_progress(
        channel_progress(first_lifecycle_review_id=epoch_ms(NOW - DAY))
    )
    local_now = NOW.astimezone(timezone(timedelta(hours=7)))
    assert decide_transitions(progress, now=local_now) == decide_transitions(
        progress,
        now=NOW,
    )


def test_assessment_timestamp_must_be_normalized_and_not_future() -> None:
    non_normalized = replace(
        assessment("a1", NOW - DAY),
        ts="2026-08-21T19:00:00+07:00",
    )
    future = assessment("a2", NOW + timedelta(milliseconds=1))

    with pytest.raises(ReconcileDecisionError, match="normalized UTC"):
        decide_transitions(
            unit_progress(stable_channel_with_assessments(non_normalized)),
            now=NOW,
        )
    with pytest.raises(ReconcileDecisionError, match="future"):
        decide_transitions(
            unit_progress(stable_channel_with_assessments(future)),
            now=NOW,
        )


def test_new_without_review_is_noop() -> None:
    decision = decide_transitions(
        unit_progress(channel_progress()),
        now=NOW,
    )

    assert decision.transitions == ()
    assert decision.suspend_card_ids == ()


def test_new_first_review_plans_exactly_one_step_to_learning() -> None:
    first_review_id = epoch_ms(NOW - 40 * DAY)
    channel = channel_progress(
        first_lifecycle_review_id=first_review_id,
        latest_lifecycle_review_id=first_review_id,
        interval_days=STABLE_MIN_INTERVAL_DAYS,
        age_days=STABLE_MIN_AGE_DAYS,
    )

    decision = decide_transitions(unit_progress(channel), now=NOW)

    assert len(decision.transitions) == 1
    transition = decision.transitions[0]
    assert (transition.from_state, transition.to_state, transition.trigger) == (
        "NEW",
        "LEARNING",
        "FIRST_REVIEW",
    )
    assert transition.evidence == {
        "first_lifecycle_review_id": first_review_id
    }
    assert transition.transition_id == expected_transition_id(transition)


def test_learning_all_stability_gates_produce_exact_stable_evidence() -> None:
    channel = stable_learning_channel()

    transition = decide_transitions(
        unit_progress(channel),
        now=NOW,
    ).transitions[0]

    first_instant = datetime.fromtimestamp(
        channel.first_lifecycle_review_id / 1000,
        tz=timezone.utc,
    )
    assert (transition.from_state, transition.to_state, transition.trigger) == (
        "LEARNING",
        "STABLE",
        "STABILITY_GATE",
    )
    assert transition.evidence == {
        "first_lifecycle_review_id": channel.first_lifecycle_review_id,
        "latest_lifecycle_review_id": channel.latest_lifecycle_review_id,
        "latest_lapse_review_id": None,
        "interval_days": STABLE_MIN_INTERVAL_DAYS,
        "eligibility_boundary": (
            first_instant + STABLE_MIN_AGE_DAYS * DAY
        ).isoformat(),
    }
    assert transition.transition_id == expected_transition_id(transition)


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_days": STABLE_MIN_INTERVAL_DAYS - 1},
        {"age_days": STABLE_MIN_AGE_DAYS - 1},
        {"lapses_last_30_days": 1},
        {"is_suspended": True},
    ],
    ids=["interval", "age", "recent-lapse", "suspended"],
)
def test_learning_missing_one_stability_gate_is_noop(
    changes: dict[str, object],
) -> None:
    channel = stable_learning_channel(**changes)
    assert decide_transitions(
        unit_progress(channel),
        now=NOW,
    ).transitions == ()


def test_stability_exact_age_boundary_is_eligible() -> None:
    first = NOW - STABLE_MIN_AGE_DAYS * DAY
    channel = stable_learning_channel(
        state_entered_at=first.isoformat(),
        first_lifecycle_review_id=epoch_ms(first),
        latest_lifecycle_review_id=epoch_ms(NOW),
    )

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert transition.evidence["eligibility_boundary"] == NOW.isoformat()


def test_stability_exact_lapse_clear_boundary_is_eligible() -> None:
    lapse = NOW - STABLE_ZERO_LAPSE_WINDOW_DAYS * DAY
    channel = stable_learning_channel(
        latest_lapse_review_id=epoch_ms(lapse),
        lapses_last_30_days=0,
    )

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert transition.evidence["eligibility_boundary"] == NOW.isoformat()


def test_stability_one_ms_before_lapse_clear_boundary_is_noop() -> None:
    lapse = NOW - STABLE_ZERO_LAPSE_WINDOW_DAYS * DAY + timedelta(milliseconds=1)
    channel = stable_learning_channel(
        latest_lapse_review_id=epoch_ms(lapse),
        lapses_last_30_days=0,
    )

    assert decide_transitions(
        unit_progress(channel),
        now=NOW,
    ).transitions == ()


def test_stable_earliest_lapse_outranks_simultaneous_mastery() -> None:
    entry = NOW - 30 * DAY
    first_lapse = epoch_ms(entry + DAY)
    later_lapse = epoch_ms(entry + 2 * DAY)
    first_pass = assessment("p1", entry + DAY)
    second_pass = assessment(
        "p2",
        entry + (1 + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS) * DAY,
    )
    channel = stable_channel_with_assessments(
        first_pass,
        second_pass,
        state_entry=entry,
        first_lapse_after_state_entry_id=first_lapse,
        latest_lapse_review_id=later_lapse,
    )

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert (transition.to_state, transition.trigger) == (
        "LEARNING",
        "REVIEW_LAPSE",
    )
    assert transition.evidence == {"lapse_revlog_id": first_lapse}


def test_two_delayed_novel_passes_master_stable_channel() -> None:
    entry = NOW - 30 * DAY
    first = assessment("p1", entry + DAY)
    second = assessment(
        "p2",
        entry + (1 + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS) * DAY,
    )
    channel = stable_channel_with_assessments(first, second, state_entry=entry)

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert (transition.to_state, transition.trigger) == (
        "MASTERED",
        "MASTERY_ASSESSMENT_PASS",
    )
    assert transition.evidence == {
        "assessment_ids": ["p1", "p2"],
        "stimulus_refs": ["stimulus:p1", "stimulus:p2"],
        "decisive_assessment_id": "p2",
    }


def test_pass_one_ms_short_does_not_advance_but_later_pass_may() -> None:
    entry = NOW - 30 * DAY
    first_at = entry + DAY
    first = assessment("p1", first_at)
    too_soon = assessment(
        "p2",
        first_at + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS * DAY
        - timedelta(milliseconds=1),
    )
    later = assessment(
        "p3",
        first_at + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS * DAY,
    )

    assert decide_transitions(
        unit_progress(
            stable_channel_with_assessments(first, too_soon, state_entry=entry)
        ),
        now=NOW,
    ).transitions == ()
    transition = decide_transitions(
        unit_progress(
            stable_channel_with_assessments(
                first,
                too_soon,
                later,
                state_entry=entry,
            )
        ),
        now=NOW,
    ).transitions[0]
    assert transition.evidence["assessment_ids"] == ["p1", "p3"]


def test_novel_failure_resets_mastery_streak() -> None:
    entry = NOW - 40 * DAY
    first = assessment("old-pass", entry + DAY)
    failure = assessment("fail", entry + 9 * DAY, passed=False)
    second = assessment("new-pass-1", entry + 10 * DAY)
    third = assessment(
        "new-pass-2",
        entry + (10 + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS) * DAY,
    )

    transition = decide_transitions(
        unit_progress(
            stable_channel_with_assessments(
                first,
                failure,
                second,
                third,
                state_entry=entry,
            )
        ),
        now=NOW,
    ).transitions[0]
    assert transition.evidence["assessment_ids"] == ["new-pass-1", "new-pass-2"]


@pytest.mark.parametrize("passed", [True, False], ids=["pass", "fail"])
def test_non_novel_assessment_is_ignored_by_mastery_streak(passed: bool) -> None:
    entry = NOW - 30 * DAY
    first = assessment("p1", entry + DAY)
    ignored = assessment(
        "ignored",
        entry + 2 * DAY,
        passed=passed,
        novel=False,
    )
    second = assessment(
        "p2",
        entry + (1 + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS) * DAY,
    )

    transition = decide_transitions(
        unit_progress(
            stable_channel_with_assessments(
                first,
                ignored,
                second,
                state_entry=entry,
            )
        ),
        now=NOW,
    ).transitions[0]
    assert transition.evidence["assessment_ids"] == ["p1", "p2"]


def test_duplicate_assessment_id_in_mastery_streak_fails_closed() -> None:
    entry = NOW - 30 * DAY
    first = assessment("duplicate", entry + DAY)
    duplicate = assessment(
        "duplicate",
        entry + (1 + MASTERED_MIN_DELAY_BETWEEN_PASSES_DAYS) * DAY,
        stimulus_ref="different-stimulus",
    )

    with pytest.raises(ReconcileDecisionError, match="duplicate assessment_id"):
        decide_transitions(
            unit_progress(
                stable_channel_with_assessments(first, duplicate, state_entry=entry)
            ),
            now=NOW,
        )


def test_repeated_stimulus_does_not_count_twice() -> None:
    entry = NOW - 40 * DAY
    first = assessment("p1", entry + DAY, stimulus_ref="shared")
    repeated = assessment("p2", entry + 8 * DAY, stimulus_ref="shared")
    later = assessment("p3", entry + 15 * DAY, stimulus_ref="distinct")

    transition = decide_transitions(
        unit_progress(
            stable_channel_with_assessments(
                first,
                repeated,
                later,
                state_entry=entry,
            )
        ),
        now=NOW,
    ).transitions[0]
    assert transition.evidence["assessment_ids"] == ["p1", "p3"]
    assert transition.evidence["stimulus_refs"] == ["shared", "distinct"]


def test_earliest_mastery_sequence_and_id_ignore_later_assessments() -> None:
    entry = NOW - 40 * DAY
    first = assessment("p1", entry + DAY)
    second = assessment("p2", entry + 8 * DAY)
    later = assessment("p3", entry + 15 * DAY)
    early_progress = unit_progress(
        stable_channel_with_assessments(first, second, state_entry=entry)
    )
    later_progress = unit_progress(
        stable_channel_with_assessments(first, second, later, state_entry=entry)
    )

    early = decide_transitions(early_progress, now=NOW).transitions[0]
    with_later = decide_transitions(later_progress, now=NOW).transitions[0]
    assert with_later.evidence == early.evidence
    assert with_later.transition_id == early.transition_id


@pytest.mark.parametrize(
    "items",
    [
        (assessment("pass", NOW - DAY, passed=True),),
        (assessment("not-novel", NOW - DAY, passed=False, novel=False),),
        (assessment("equal", NOW - 20 * DAY, passed=False),),
    ],
    ids=["pass", "non-novel-fail", "equal-time-fail"],
)
def test_mastered_nonqualifying_assessment_is_noop(
    items: tuple[LifecycleAssessment, ...],
) -> None:
    entry = NOW - 20 * DAY
    channel = stable_channel_with_assessments(
        *items,
        state="MASTERED",
        state_entry=entry,
    )
    assert decide_transitions(
        unit_progress(channel),
        now=NOW,
    ).transitions == ()


def test_mastered_uses_earliest_novel_post_entry_failure() -> None:
    entry = NOW - 20 * DAY
    before = assessment("before", entry - DAY, passed=False)
    earliest = assessment("earliest", entry + DAY, passed=False)
    later = assessment("later", entry + 2 * DAY, passed=False)
    channel = stable_channel_with_assessments(
        later,
        before,
        earliest,
        state="MASTERED",
        state_entry=entry,
    )

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert (transition.to_state, transition.trigger) == (
        "RELAPSE",
        "ASSESSMENT_FAIL",
    )
    assert transition.evidence == {"assessment_id": "earliest"}


def test_dormant_failure_reports_reactivation_without_unsuspending() -> None:
    entry = NOW - 20 * DAY
    failure = assessment("failed", entry + DAY, passed=False)
    channel = stable_channel_with_assessments(
        failure,
        state="DORMANT",
        state_entry=entry,
        is_suspended=True,
        card_id=812,
    )

    decision = decide_transitions(unit_progress(channel), now=NOW)
    assert decision.transitions[0].to_state == "RELAPSE"
    assert decision.reactivation_required_card_ids == (812,)
    assert decision.suspend_card_ids == ()
    assert channel.is_suspended is True

    active_channel = replace(channel, is_suspended=False)
    active_decision = decide_transitions(
        unit_progress(active_channel),
        now=NOW,
    )
    assert active_decision.transitions[0].to_state == "RELAPSE"
    assert active_decision.reactivation_required_card_ids == ()


def test_dormant_equal_or_non_novel_failure_is_noop() -> None:
    entry = NOW - 20 * DAY
    equal = assessment("equal", entry, passed=False)
    non_novel = assessment("not-novel", entry + DAY, passed=False, novel=False)
    channel = stable_channel_with_assessments(
        equal,
        non_novel,
        state="DORMANT",
        state_entry=entry,
        is_suspended=True,
    )
    decision = decide_transitions(unit_progress(channel), now=NOW)
    assert decision.transitions == ()
    assert decision.reactivation_required_card_ids == ()


def test_relapse_uses_first_post_entry_lifecycle_review() -> None:
    entry = NOW - 10 * DAY
    first_review = epoch_ms(entry + timedelta(seconds=3))
    later_review = epoch_ms(entry + timedelta(seconds=5))
    channel = channel_progress(
        "R",
        "RELAPSE",
        state_entered_at=entry.isoformat(),
        first_lifecycle_review_after_state_entry_id=first_review,
        latest_lifecycle_review_id=later_review,
    )

    transition = decide_transitions(unit_progress(channel), now=NOW).transitions[0]
    assert (transition.to_state, transition.trigger) == (
        "LEARNING",
        "RELAPSE_REVIEW",
    )
    assert transition.evidence == {"review_revlog_id": first_review}


def mastered_pair(
    *,
    r_card_id: int = 900,
    l_card_id: int = 100,
    r_assessments: tuple[LifecycleAssessment, ...] = (),
) -> tuple[ChannelProgress, ChannelProgress, str]:
    r_entry = NOW - 35 * DAY
    l_entry = NOW - 30 * DAY
    r = channel_progress(
        "R",
        "MASTERED",
        card_id=r_card_id,
        state_entered_at=r_entry.isoformat(),
        state_episode_id="r" * 64,
        assessments=r_assessments,
    )
    l = channel_progress(
        "L",
        "MASTERED",
        card_id=l_card_id,
        state_entered_at=l_entry.isoformat(),
        state_episode_id="l" * 64,
    )
    return r, l, l_entry.isoformat()


def test_exact_dormancy_boundary_plans_all_members_and_suspension() -> None:
    r, l, all_mastered_at = mastered_pair()
    progress = unit_progress(l, r, all_mastered_at=all_mastered_at)

    decision = decide_transitions(progress, now=NOW)

    assert tuple(item.channel for item in decision.transitions) == ("R", "L")
    assert all(item.to_state == "DORMANT" for item in decision.transitions)
    assert decision.suspend_card_ids == (900, 100)
    expected_evidence = {
        "mastered_entry_transition_ids": {"R": "r" * 64, "L": "l" * 64},
        "all_channels_mastered_at": all_mastered_at,
        "eligibility_boundary": NOW.isoformat(),
    }
    assert all(
        item.evidence == expected_evidence for item in decision.transitions
    )
    assert all(
        item.transition_id == expected_transition_id(item)
        for item in decision.transitions
    )
    expected_group_id = canonical_digest(
        {
            "kind": T9_DORMANCY_GROUP_KIND,
            "unit_key": UNIT_KEY,
            "member_transition_ids": sorted(
                item.transition_id for item in decision.transitions
            ),
        }
    )
    assert {
        item.transition_group_id for item in decision.transitions
    } == {expected_group_id}


def test_dormancy_one_ms_before_boundary_is_noop() -> None:
    r, l, all_mastered_at = mastered_pair()
    decision = decide_transitions(
        unit_progress(r, l, all_mastered_at=all_mastered_at),
        now=NOW - timedelta(milliseconds=1),
    )
    assert decision.transitions == ()
    assert decision.suspend_card_ids == ()


def test_one_non_mastered_channel_prevents_dormancy() -> None:
    r, l, all_mastered_at = mastered_pair()
    l = replace(
        l,
        state="STABLE",
        state_episode_id="stable-l",
    )
    decision = decide_transitions(
        unit_progress(r, l, all_mastered_at=all_mastered_at),
        now=NOW,
    )
    assert decision.transitions == ()
    assert decision.suspend_card_ids == ()


def test_mastered_failure_prevents_dormancy_and_only_relapses_failed_channel() -> None:
    failure = assessment(
        "failed-r",
        NOW - 34 * DAY,
        passed=False,
    )
    r, l, all_mastered_at = mastered_pair(r_assessments=(failure,))
    decision = decide_transitions(
        unit_progress(r, l, all_mastered_at=all_mastered_at),
        now=NOW,
    )
    assert tuple(item.channel for item in decision.transitions) == ("R",)
    assert decision.transitions[0].to_state == "RELAPSE"
    assert decision.suspend_card_ids == ()


def test_dormancy_group_identity_excludes_card_ids() -> None:
    r1, l1, all_mastered_at = mastered_pair(r_card_id=900, l_card_id=100)
    r2, l2, _same_time = mastered_pair(r_card_id=1, l_card_id=999)
    first = decide_transitions(
        unit_progress(r1, l1, all_mastered_at=all_mastered_at),
        now=NOW,
    )
    second = decide_transitions(
        unit_progress(l2, r2, all_mastered_at=all_mastered_at),
        now=NOW,
    )
    assert tuple(item.transition_id for item in first.transitions) == tuple(
        item.transition_id for item in second.transitions
    )
    assert first.transitions[0].transition_group_id == (
        second.transitions[0].transition_group_id
    )
    assert first.suspend_card_ids == (900, 100)
    assert second.suspend_card_ids == (1, 999)


def test_transition_id_changes_only_with_decisive_evidence() -> None:
    first_id = epoch_ms(NOW - 40 * DAY)
    later_id = epoch_ms(NOW - 39 * DAY)
    first = unit_progress(
        channel_progress(first_lifecycle_review_id=first_id)
    )
    changed = unit_progress(
        channel_progress(first_lifecycle_review_id=later_id)
    )
    first_transition = decide_transitions(first, now=NOW).transitions[0]
    repeated_transition = decide_transitions(first, now=NOW + DAY).transitions[0]
    changed_transition = decide_transitions(changed, now=NOW).transitions[0]
    assert repeated_transition.transition_id == first_transition.transition_id
    assert changed_transition.transition_id != first_transition.transition_id


def test_stability_id_does_not_change_after_boundary_is_crossed() -> None:
    progress = unit_progress(stable_learning_channel())
    first = decide_transitions(progress, now=NOW).transitions[0]
    later = decide_transitions(progress, now=NOW + 5 * DAY).transitions[0]
    assert later.evidence == first.evidence
    assert later.transition_id == first.transition_id


def test_leech_is_ordered_diagnostic_only() -> None:
    r = channel_progress("R", lapses_total=ANKI_LEECH_THRESHOLD)
    l = channel_progress("L", lapses_total=ANKI_LEECH_THRESHOLD - 1)
    w = channel_progress("W", lapses_total=ANKI_LEECH_THRESHOLD + 2)
    decision = decide_transitions(
        unit_progress(w, l, r, has_leech_tag=True),
        now=NOW,
    )
    assert decision.leech_rescue_channels == ("R", "W")
    assert decision.transitions == ()
    assert decision.suspend_card_ids == ()


def test_multiple_independent_transitions_follow_channel_order() -> None:
    r_review = epoch_ms(NOW - DAY)
    relapse_entry = NOW - 10 * DAY
    l_review = epoch_ms(relapse_entry + DAY)
    r = channel_progress("R", first_lifecycle_review_id=r_review)
    l = channel_progress(
        "L",
        "RELAPSE",
        state_entered_at=relapse_entry.isoformat(),
        first_lifecycle_review_after_state_entry_id=l_review,
    )
    decision = decide_transitions(unit_progress(l, r), now=NOW)
    assert tuple(item.channel for item in decision.transitions) == ("R", "L")
    assert len({item.channel for item in decision.transitions}) == 2


def test_decision_is_pure_and_leaves_input_unchanged() -> None:
    progress = unit_progress(
        channel_progress(
            first_lifecycle_review_id=epoch_ms(NOW - DAY),
        )
    )
    before = deepcopy(progress)
    parameters = tuple(inspect.signature(decide_transitions).parameters)

    decide_transitions(progress, now=NOW)

    assert progress == before
    assert parameters == ("progress", "now")
    assert "anki" not in parameters
    assert "event_log" not in parameters
