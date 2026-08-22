"""Read-only T9 observation of trustworthy lifecycle evidence.

T9.1 performs no transition decisions and no persistence. Its only public
operation transforms explicit Anki and EventLog reads into the frozen
``UnitProgress`` observation model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Protocol, cast

from .anki import AnkiConnectClient, AnkiConnectError
from .anki_template import verify_model_snapshot
from .contracts import (
    ANKI_LEECH_TAG,
    ANKI_NOTE_TYPE_NAME,
    ANKI_QUEUE_SUSPENDED,
    CHANNELS,
    CHANNEL_BY_TEMPLATE_NAME,
    INITIAL_NEW_EPISODE_PREFIX,
    LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS,
    LIFECYCLE_SECONDS_PER_DAY,
    NOTE_FIELDS,
    REVLOG_EASE_AGAIN,
    REVLOG_LIFECYCLE_TYPES,
    REVLOG_TYPE_CRAM,
    REVLOG_TYPE_LEARNING,
    REVLOG_TYPE_RELEARNING,
    REVLOG_TYPE_REVIEW,
    STATE_MASTERED,
    STATE_NEW,
    STATE_TRANSITIONS,
    STATE_TRIGGERS,
    STATES,
    STABLE_ZERO_LAPSE_WINDOW_DAYS,
    T9_STATE_OPTIONAL_PAYLOAD_FIELDS,
    T9_STATE_PHASE_ABORT,
    T9_STATE_PHASE_COMMIT,
    T9_STATE_PHASE_PREPARE,
    T9_STATE_REQUIRED_PAYLOAD_FIELDS,
)
from .models import (
    ChannelProgress,
    Event,
    LifecycleAssessment,
    UnitProgress,
    VocabUnit,
)
from .validators import validate_forge_unit


class _EventLogReader(Protocol):
    def read(self) -> list[Event]:
        """Return decoded events without writing."""


class ReconcileObservationError(RuntimeError):
    """Base class for fail-closed T9 observation errors."""


class ReconcileNoteError(ReconcileObservationError):
    """The requested note cannot be trusted as a current VocabularyUnit."""


class ReconcileCardError(ReconcileObservationError):
    """Current card identity, attribution, or scheduling data is ambiguous."""


class ReconcileRevlogError(ReconcileObservationError):
    """Review history cannot be interpreted under the frozen T9 contract."""


class ReconcileEventHistoryError(ReconcileObservationError):
    """Lifecycle event history cannot establish trustworthy provenance."""


_KNOWN_REVLOG_TYPES = frozenset(
    (
        REVLOG_TYPE_LEARNING,
        REVLOG_TYPE_REVIEW,
        REVLOG_TYPE_RELEARNING,
        REVLOG_TYPE_CRAM,
    )
)
_TRANSITION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_MARKER_FIELDS = frozenset(
    (
        "transition_id",
        "phase",
        *T9_STATE_OPTIONAL_PAYLOAD_FIELDS,
    )
)


def _lapse_window() -> timedelta:
    return timedelta(
        seconds=STABLE_ZERO_LAPSE_WINDOW_DAYS * LIFECYCLE_SECONDS_PER_DAY
    )


def observe_unit(
    note_id: int,
    *,
    anki: AnkiConnectClient,
    event_log: _EventLogReader,
    now: datetime,
) -> UnitProgress:
    """Return one deterministic, read-only T9 observation snapshot."""
    now_utc = _require_now(now)
    unit, card_ids, has_leech_tag = _load_note(note_id, anki)

    try:
        model_snapshot = anki.verified_note_type_snapshot()
    except AnkiConnectError as exc:
        raise ReconcileCardError(
            "VocabularyUnit model snapshot could not be verified"
        ) from exc
    ordinal_to_template = _verified_ordinal_map(model_snapshot)
    cards_by_channel = _load_cards(
        note_id,
        unit,
        card_ids,
        ordinal_to_template,
        anki,
    )
    revlog_by_channel = _load_revlog(
        card_ids,
        cards_by_channel,
        anki,
        now_utc,
    )

    active_states = unit.active_channel_states()
    assessments_by_channel, episode_ids, episode_entries = _load_event_history(
        unit.unit_key,
        active_states,
        event_log,
        now_utc,
    )

    channels = tuple(
        _channel_progress(
            channel,
            active_states[channel],
            cards_by_channel[channel],
            revlog_by_channel[channel],
            episode_ids[channel],
            assessments_by_channel[channel],
        )
        for channel in CHANNELS
        if channel in active_states
    )

    all_mastered_at = ""
    if channels and all(
        channel.state == STATE_MASTERED for channel in channels
    ):
        entries = tuple(
            episode_entries[channel.channel] for channel in channels
        )
        if any(entry is None for entry in entries):
            raise ReconcileEventHistoryError(
                "MASTERED channels require committed entry provenance"
            )
        all_mastered_at = max(
            entry for entry in entries if entry is not None
        ).isoformat()

    return UnitProgress(
        unit_key=unit.unit_key,
        channels=channels,
        all_active_channels_mastered_at=all_mastered_at,
        has_leech_tag=has_leech_tag,
    )


def _require_now(now: datetime) -> datetime:
    if not isinstance(now, datetime):
        raise ReconcileObservationError("now must be an aware datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ReconcileObservationError("now must include a timezone offset")
    return now.astimezone(timezone.utc)


def _load_note(
    note_id: int,
    anki: AnkiConnectClient,
) -> tuple[VocabUnit, tuple[int, ...], bool]:
    if type(note_id) is not int:
        raise ReconcileNoteError("note_id must be an actual integer")

    try:
        notes = anki.notes_info([note_id])
    except AnkiConnectError as exc:
        raise ReconcileNoteError("notesInfo failed for the requested note") from exc
    if not isinstance(notes, list) or len(notes) != 1:
        raise ReconcileNoteError(
            "notesInfo must return exactly one note for the requested ID"
        )

    note = notes[0]
    if not isinstance(note, Mapping):
        raise ReconcileNoteError("notesInfo note must be an object")
    returned_id = note.get("noteId")
    if type(returned_id) is not int or returned_id != note_id:
        raise ReconcileNoteError("notesInfo returned a different note ID")
    if note.get("modelName") != ANKI_NOTE_TYPE_NAME:
        raise ReconcileNoteError(
            f"note model must be exactly {ANKI_NOTE_TYPE_NAME!r}"
        )

    raw_fields = note.get("fields")
    if not isinstance(raw_fields, Mapping) or set(raw_fields) != set(NOTE_FIELDS):
        raise ReconcileNoteError("notesInfo fields must match NOTE_FIELDS exactly")
    values: dict[str, str] = {}
    for field_name in NOTE_FIELDS:
        record = raw_fields[field_name]
        if not isinstance(record, Mapping) or "value" not in record:
            raise ReconcileNoteError(
                f"notesInfo field {field_name!r} must contain a value"
            )
        value = record["value"]
        if not isinstance(value, str):
            raise ReconcileNoteError(
                f"notesInfo field {field_name!r} value must be a string"
            )
        values[field_name] = value

    unit = VocabUnit(**values)
    violations = validate_forge_unit(unit)
    if violations:
        raise ReconcileNoteError(
            "VocabularyUnit fails Forge/current target-state invariants: "
            f"{violations}"
        )

    raw_cards = note.get("cards")
    if not isinstance(raw_cards, list) or any(
        type(card_id) is not int for card_id in raw_cards
    ):
        raise ReconcileCardError("notesInfo cards must be a list of integer IDs")
    if len(set(raw_cards)) != len(raw_cards):
        raise ReconcileCardError("notesInfo contains duplicate card IDs")

    raw_tags = note.get("tags")
    if not isinstance(raw_tags, list) or any(
        not isinstance(tag, str) for tag in raw_tags
    ):
        raise ReconcileNoteError("notesInfo tags must be a list of strings")

    return unit, tuple(raw_cards), ANKI_LEECH_TAG in raw_tags


def _verified_ordinal_map(snapshot: object) -> dict[int, str]:
    violations = verify_model_snapshot(snapshot)
    if violations:
        raise ReconcileCardError(
            "VocabularyUnit model snapshot is not semantically verified: "
            + "; ".join(str(violation) for violation in violations)
        )
    if not isinstance(snapshot, Mapping):
        raise ReconcileCardError("verified model snapshot must be an object")
    templates = snapshot.get("tmpls")
    if not isinstance(templates, list):
        raise ReconcileCardError("verified model templates must be a list")

    ordinal_to_template: dict[int, str] = {}
    seen_names: set[str] = set()
    for template in templates:
        if not isinstance(template, Mapping):
            raise ReconcileCardError("verified model template must be an object")
        ordinal = template.get("ord")
        name = template.get("name")
        if (
            type(ordinal) is not int
            or ordinal < 0
            or not isinstance(name, str)
            or name not in CHANNEL_BY_TEMPLATE_NAME
        ):
            raise ReconcileCardError(
                "verified model template ordinal/name is malformed"
            )
        if ordinal in ordinal_to_template or name in seen_names:
            raise ReconcileCardError(
                "verified model has duplicate template ordinal or name"
            )
        ordinal_to_template[ordinal] = name
        seen_names.add(name)

    if seen_names != set(CHANNEL_BY_TEMPLATE_NAME):
        raise ReconcileCardError(
            "verified model template names do not match channel contracts"
        )
    return ordinal_to_template


def _load_cards(
    note_id: int,
    unit: VocabUnit,
    card_ids: tuple[int, ...],
    ordinal_to_template: Mapping[int, str],
    anki: AnkiConnectClient,
) -> dict[str, dict[str, Any]]:
    try:
        rows = anki.cards_info(list(card_ids))
    except AnkiConnectError as exc:
        raise ReconcileCardError("cardsInfo failed for note cards") from exc
    if not isinstance(rows, list) or len(rows) != len(card_ids):
        raise ReconcileCardError(
            "cardsInfo cardinality must exactly match requested card IDs"
        )

    expected_ids = set(card_ids)
    seen_ids: set[int] = set()
    by_channel: dict[str, dict[str, Any]] = {}
    active_states = unit.active_channel_states()

    for row in rows:
        if not isinstance(row, Mapping):
            raise ReconcileCardError("cardsInfo row must be an object")
        card_id = _actual_int(row.get("cardId"), "cardsInfo cardId")
        card_note_id = _actual_int(row.get("note"), "cardsInfo note")
        ordinal = _actual_int(row.get("ord"), "cardsInfo ord")
        interval = _actual_int(row.get("interval"), "cardsInfo interval")
        lapses = _actual_int(row.get("lapses"), "cardsInfo lapses")
        queue = _actual_int(row.get("queue"), "cardsInfo queue")

        if card_id not in expected_ids:
            raise ReconcileCardError(f"cardsInfo returned unknown card ID {card_id}")
        if card_id in seen_ids:
            raise ReconcileCardError(f"cardsInfo duplicated card ID {card_id}")
        seen_ids.add(card_id)
        if card_note_id != note_id:
            raise ReconcileCardError(
                f"card {card_id} belongs to another note {card_note_id}"
            )
        if ordinal < 0:
            raise ReconcileCardError("cardsInfo ord must be non-negative")
        if interval < 0:
            raise ReconcileCardError(
                "cardsInfo interval must be a non-negative day count"
            )
        if lapses < 0:
            raise ReconcileCardError("cardsInfo lapses must be non-negative")

        template_name = ordinal_to_template.get(ordinal)
        if template_name is None:
            raise ReconcileCardError(
                f"card {card_id} has unknown template ordinal {ordinal}"
            )
        channel = CHANNEL_BY_TEMPLATE_NAME.get(template_name)
        if channel is None:
            raise ReconcileCardError(
                f"card {card_id} has unknown template name {template_name!r}"
            )
        if channel not in active_states:
            raise ReconcileCardError(
                f"card {card_id} exists for disabled channel {channel}"
            )
        if channel in by_channel:
            raise ReconcileCardError(
                f"multiple cards resolve to enabled channel {channel}"
            )

        by_channel[channel] = {
            "card_id": card_id,
            "template_name": template_name,
            "template_ordinal": ordinal,
            "interval_days": interval,
            "lapses_total": lapses,
            "is_suspended": queue == ANKI_QUEUE_SUSPENDED,
        }

    if seen_ids != expected_ids:
        missing_ids = tuple(sorted(expected_ids.difference(seen_ids)))
        raise ReconcileCardError(f"cardsInfo omitted card IDs {missing_ids}")
    missing_channels = tuple(
        channel
        for channel in CHANNELS
        if channel in active_states and channel not in by_channel
    )
    if missing_channels:
        raise ReconcileCardError(
            f"enabled channels have no attributed card: {missing_channels}"
        )
    return by_channel


def _actual_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ReconcileCardError(f"{field_name} must be an actual integer")
    return value


def _load_revlog(
    card_ids: tuple[int, ...],
    cards_by_channel: Mapping[str, Mapping[str, Any]],
    anki: AnkiConnectClient,
    now_utc: datetime,
) -> dict[str, dict[str, int | None]]:
    try:
        raw_revlog = anki.get_revlog(list(card_ids))
    except AnkiConnectError as exc:
        raise ReconcileRevlogError("getReviewsOfCards failed") from exc
    if not isinstance(raw_revlog, Mapping):
        raise ReconcileRevlogError("revlog result must be a card-keyed map")

    expected_keys = {str(card_id) for card_id in card_ids}
    if set(raw_revlog) != expected_keys:
        missing = tuple(sorted(expected_keys.difference(raw_revlog)))
        unexpected = tuple(sorted(set(raw_revlog).difference(expected_keys)))
        raise ReconcileRevlogError(
            "revlog card keys must exactly match requested cards; "
            f"missing={missing}, unexpected={unexpected}"
        )

    card_to_channel = {
        cast(int, card["card_id"]): channel
        for channel, card in cards_by_channel.items()
    }
    seen_review_ids: set[int] = set()
    by_channel: dict[str, dict[str, int | None]] = {}

    for card_id in card_ids:
        reviews = raw_revlog[str(card_id)]
        if not isinstance(reviews, list):
            raise ReconcileRevlogError(
                f"revlog for card {card_id} must be a list"
            )

        parsed: list[tuple[int, int, int, int, datetime]] = []
        for review in reviews:
            if not isinstance(review, Mapping):
                raise ReconcileRevlogError("each revlog entry must be an object")
            review_id = _revlog_int(review.get("id"), "id")
            ease = _revlog_int(review.get("ease"), "ease")
            review_type = _revlog_int(review.get("type"), "type")
            interval = _revlog_int(review.get("ivl"), "ivl")
            if review_id < 0:
                raise ReconcileRevlogError("revlog id must be non-negative")
            if ease not in (1, 2, 3, 4):
                raise ReconcileRevlogError("revlog ease must be in 1..4")
            if review_type not in _KNOWN_REVLOG_TYPES:
                raise ReconcileRevlogError(
                    f"unknown revlog type {review_type}"
                )
            if review_id in seen_review_ids:
                raise ReconcileRevlogError(
                    f"duplicate revlog id {review_id} is ambiguous"
                )
            seen_review_ids.add(review_id)
            instant = _revlog_instant(review_id)
            if instant > now_utc:
                raise ReconcileRevlogError(
                    f"revlog id {review_id} is in the future"
                )
            parsed.append((review_id, ease, review_type, interval, instant))

        parsed.sort(key=lambda item: item[0])
        lifecycle = tuple(
            item for item in parsed if item[2] in REVLOG_LIFECYCLE_TYPES
        )
        lapses = tuple(
            item
            for item in lifecycle
            if item[2] == REVLOG_TYPE_REVIEW
            and item[1] == REVLOG_EASE_AGAIN
        )
        first = lifecycle[0] if lifecycle else None
        latest = lifecycle[-1] if lifecycle else None
        latest_lapse = lapses[-1] if lapses else None
        window_start = now_utc - _lapse_window()

        channel = card_to_channel[card_id]
        by_channel[channel] = {
            "first_lifecycle_review_id": None if first is None else first[0],
            "latest_lifecycle_review_id": None if latest is None else latest[0],
            "latest_lapse_review_id": (
                None if latest_lapse is None else latest_lapse[0]
            ),
            "lapses_last_30_days": sum(
                1 for lapse in lapses if lapse[4] >= window_start
            ),
            "age_days": (
                0
                if first is None
                else int(
                    (now_utc - first[4]).total_seconds()
                    // LIFECYCLE_SECONDS_PER_DAY
                )
            ),
        }

    return by_channel


def _revlog_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ReconcileRevlogError(
            f"revlog {field_name} must be an actual integer"
        )
    return value


def _revlog_instant(review_id: int) -> datetime:
    try:
        return datetime.fromtimestamp(review_id / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ReconcileRevlogError(
            f"revlog id {review_id} is not a valid epoch-millisecond timestamp"
        ) from exc


def _load_event_history(
    unit_key: str,
    active_states: Mapping[str, str],
    event_log: _EventLogReader,
    now_utc: datetime,
) -> tuple[
    dict[str, tuple[LifecycleAssessment, ...]],
    dict[str, str],
    dict[str, datetime | None],
]:
    try:
        events = event_log.read()
    except (OSError, TypeError, ValueError) as exc:
        raise ReconcileEventHistoryError("EventLog history cannot be read") from exc
    if not isinstance(events, list) or any(
        not isinstance(event, Event) for event in events
    ):
        raise ReconcileEventHistoryError(
            "EventLog.read() must return a list of Event values"
        )

    assessments: dict[
        str, list[tuple[datetime, int, LifecycleAssessment]]
    ] = {channel: [] for channel in active_states}
    journal_records: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        if event.unit_key != unit_key:
            continue
        if event.event == "JUDGE":
            parsed = _lifecycle_assessment(event, index, now_utc)
            if parsed is not None and parsed[2].channel in assessments:
                assessments[parsed[2].channel].append(parsed)
        elif event.event == "STATE":
            record = _journal_record(event, index, now_utc)
            if record is not None:
                journal_records.append(record)

    ordered_assessments = {
        channel: tuple(
            item[2]
            for item in sorted(values, key=lambda item: (item[0], item[1]))
        )
        for channel, values in assessments.items()
    }
    episode_ids, episode_entries = _state_episode_provenance(
        unit_key,
        active_states,
        journal_records,
    )
    return ordered_assessments, episode_ids, episode_entries


def _lifecycle_assessment(
    event: Event,
    index: int,
    now_utc: datetime,
) -> tuple[datetime, int, LifecycleAssessment] | None:
    payload = event.payload
    claimed_fields = set(payload).intersection(
        LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS
    )
    if not claimed_fields:
        return None
    if not set(LIFECYCLE_JUDGE_REQUIRED_PAYLOAD_FIELDS).issubset(payload):
        raise ReconcileEventHistoryError(
            "JUDGE claiming lifecycle fields must contain the complete D35 set"
        )

    channel = payload.get("channel")
    passed = payload.get("passed")
    assessment_id = payload.get("assessment_id")
    stimulus_ref = payload.get("stimulus_ref")
    novel = payload.get("novel")
    model_id = payload.get("model_id")
    model_version = payload.get("model_version")
    if channel not in CHANNELS:
        raise ReconcileEventHistoryError(
            "lifecycle JUDGE channel must be a frozen channel"
        )
    if type(passed) is not bool or type(novel) is not bool:
        raise ReconcileEventHistoryError(
            "lifecycle JUDGE passed and novel must be actual booleans"
        )
    for field_name, value in (
        ("assessment_id", assessment_id),
        ("stimulus_ref", stimulus_ref),
        ("model_id", model_id),
        ("model_version", model_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ReconcileEventHistoryError(
                f"lifecycle JUDGE {field_name} must be a non-empty string"
            )

    instant = _event_instant(event.ts, "JUDGE")
    if instant > now_utc:
        raise ReconcileEventHistoryError("lifecycle JUDGE timestamp is in the future")
    return (
        instant,
        index,
        LifecycleAssessment(
            channel=channel,
            passed=passed,
            assessment_id=assessment_id,
            stimulus_ref=stimulus_ref,
            novel=novel,
            ts=event.ts,
            model_id=model_id,
            model_version=model_version,
        ),
    )


def _journal_record(
    event: Event,
    index: int,
    now_utc: datetime,
) -> dict[str, Any] | None:
    payload = event.payload
    if not set(payload).intersection(_JOURNAL_MARKER_FIELDS):
        return None
    if not set(T9_STATE_REQUIRED_PAYLOAD_FIELDS).issubset(payload):
        raise ReconcileEventHistoryError(
            "STATE journal record is missing a T9 producer field"
        )

    channel = payload.get("channel")
    from_state = payload.get("from")
    to_state = payload.get("to")
    trigger = payload.get("trigger")
    transition_id = payload.get("transition_id")
    from_episode_id = payload.get("from_episode_id")
    phase = payload.get("phase")
    evidence = payload.get("evidence")
    if channel not in CHANNELS:
        raise ReconcileEventHistoryError("STATE journal channel is invalid")
    if from_state not in STATES or to_state not in STATES:
        raise ReconcileEventHistoryError("STATE journal lifecycle state is invalid")
    if (from_state, to_state) not in STATE_TRANSITIONS:
        raise ReconcileEventHistoryError("STATE journal transition is not allowed")
    if trigger not in STATE_TRIGGERS:
        raise ReconcileEventHistoryError("STATE journal trigger is invalid")
    if not isinstance(transition_id, str) or not _TRANSITION_ID_RE.fullmatch(
        transition_id
    ):
        raise ReconcileEventHistoryError(
            "STATE transition_id must be a lowercase full SHA-256 digest"
        )
    if not isinstance(from_episode_id, str) or not from_episode_id:
        raise ReconcileEventHistoryError(
            "STATE from_episode_id must be a non-empty string"
        )
    if phase not in (
        T9_STATE_PHASE_PREPARE,
        T9_STATE_PHASE_COMMIT,
        T9_STATE_PHASE_ABORT,
    ):
        raise ReconcileEventHistoryError("STATE journal phase is invalid")
    if not isinstance(evidence, dict):
        raise ReconcileEventHistoryError("STATE journal evidence must be an object")

    transition_group_id = payload.get("transition_group_id")
    if "transition_group_id" in payload and (
        not isinstance(transition_group_id, str)
        or not _TRANSITION_ID_RE.fullmatch(transition_group_id)
    ):
        raise ReconcileEventHistoryError(
            "STATE transition_group_id must be a lowercase full SHA-256 digest"
        )
    try:
        canonical_evidence = json.dumps(
            evidence,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReconcileEventHistoryError(
            "STATE journal evidence must be canonical JSON data"
        ) from exc

    transition_identity = {
        "v": event.v,
        "unit_key": event.unit_key,
        "channel": channel,
        "from": from_state,
        "to": to_state,
        "trigger": trigger,
        "from_episode_id": from_episode_id,
        "evidence": evidence,
    }
    canonical_identity = json.dumps(
        transition_identity,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_transition_id = sha256(canonical_identity.encode("utf-8")).hexdigest()
    if transition_id != expected_transition_id:
        raise ReconcileEventHistoryError(
            "STATE transition_id does not match its canonical identity"
        )

    instant = _event_instant(event.ts, "STATE")
    if instant > now_utc:
        raise ReconcileEventHistoryError("STATE journal timestamp is in the future")
    return {
        "channel": channel,
        "from": from_state,
        "to": to_state,
        "trigger": trigger,
        "transition_id": transition_id,
        "from_episode_id": from_episode_id,
        "phase": phase,
        "canonical_identity": canonical_identity,
        "canonical_evidence": canonical_evidence,
        "transition_group_id": transition_group_id,
        "instant": instant,
        "index": index,
    }


def _event_instant(value: str, event_type: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ReconcileEventHistoryError(
            f"{event_type} timestamp must be a non-empty ISO-8601 string"
        )
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReconcileEventHistoryError(
            f"{event_type} timestamp is not valid ISO-8601"
        ) from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ReconcileEventHistoryError(
            f"{event_type} timestamp must include a timezone offset"
        )
    instant = instant.astimezone(timezone.utc)
    if value != instant.isoformat():
        raise ReconcileEventHistoryError(
            f"{event_type} timestamp must be normalized UTC"
        )
    return instant


def _state_episode_provenance(
    unit_key: str,
    active_states: Mapping[str, str],
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], dict[str, datetime | None]]:
    journal_by_transition: dict[str, dict[str, object]] = {}
    committed: list[Mapping[str, Any]] = []

    for record in records:
        transition_id = cast(str, record["transition_id"])
        phase = cast(str, record["phase"])
        identity = (
            record["canonical_identity"],
            record["transition_group_id"],
        )

        if phase == T9_STATE_PHASE_PREPARE:
            if transition_id in journal_by_transition:
                raise ReconcileEventHistoryError(
                    f"STATE transition {transition_id} duplicates phase PREPARE"
                )
            journal_by_transition[transition_id] = {
                "identity": identity,
                "terminal": None,
            }
            continue

        journal = journal_by_transition.get(transition_id)
        if journal is None:
            raise ReconcileEventHistoryError(
                f"STATE transition {transition_id} terminal phase requires PREPARE"
            )
        if journal["identity"] != identity:
            raise ReconcileEventHistoryError(
                f"STATE transition {transition_id} changes identity across phases"
            )
        terminal = journal["terminal"]
        if terminal is not None:
            if terminal == phase:
                raise ReconcileEventHistoryError(
                    f"STATE transition {transition_id} duplicates phase {phase}"
                )
            raise ReconcileEventHistoryError(
                f"STATE transition {transition_id} has COMMIT and ABORT terminals"
            )
        journal["terminal"] = phase
        if phase == T9_STATE_PHASE_COMMIT:
            committed.append(record)

    committed_by_channel: dict[str, list[Mapping[str, Any]]] = {
        channel: [] for channel in active_states
    }
    for record in committed:
        channel = cast(str, record["channel"])
        if channel in committed_by_channel:
            committed_by_channel[channel].append(record)

    episode_ids: dict[str, str] = {}
    episode_entries: dict[str, datetime | None] = {}
    for channel, persisted_state in active_states.items():
        current_state = STATE_NEW
        current_episode_id = _initial_new_episode_id(unit_key, channel)
        current_entry: datetime | None = None
        for commit in committed_by_channel[channel]:
            if commit["from"] != current_state:
                raise ReconcileEventHistoryError(
                    f"STATE channel {channel} COMMIT breaks the lifecycle chain"
                )
            if commit["from_episode_id"] != current_episode_id:
                raise ReconcileEventHistoryError(
                    f"STATE channel {channel} COMMIT breaks episode provenance"
                )
            current_state = cast(str, commit["to"])
            current_episode_id = cast(str, commit["transition_id"])
            current_entry = cast(datetime, commit["instant"])

        if current_state != persisted_state:
            raise ReconcileEventHistoryError(
                f"persisted {persisted_state} channel {channel} conflicts "
                "with reconstructed journal state"
            )
        episode_ids[channel] = current_episode_id
        episode_entries[channel] = current_entry

    return episode_ids, episode_entries


def _initial_new_episode_id(unit_key: str, channel: str) -> str:
    identity = {
        "channel": channel,
        "unit_key": unit_key,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return INITIAL_NEW_EPISODE_PREFIX + sha256(canonical).hexdigest()


def _channel_progress(
    channel: str,
    state: str,
    card: Mapping[str, Any],
    revlog: Mapping[str, int | None],
    episode_id: str,
    assessments: tuple[LifecycleAssessment, ...],
) -> ChannelProgress:
    return ChannelProgress(
        channel=channel,
        state=state,
        card_id=cast(int, card["card_id"]),
        template_name=cast(str, card["template_name"]),
        template_ordinal=cast(int, card["template_ordinal"]),
        interval_days=cast(int, card["interval_days"]),
        lapses_total=cast(int, card["lapses_total"]),
        lapses_last_30_days=cast(int, revlog["lapses_last_30_days"]),
        age_days=cast(int, revlog["age_days"]),
        is_suspended=cast(bool, card["is_suspended"]),
        first_lifecycle_review_id=cast(
            int | None,
            revlog["first_lifecycle_review_id"],
        ),
        latest_lifecycle_review_id=cast(
            int | None,
            revlog["latest_lifecycle_review_id"],
        ),
        latest_lapse_review_id=cast(
            int | None,
            revlog["latest_lapse_review_id"],
        ),
        state_episode_id=episode_id,
        assessments=assessments,
    )
