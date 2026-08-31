"""Wave C v1 session composition, frozen by D71 sections 4 through 11.

The human supplies the plan; this module validates it against the existing
authorities, derives every identity from the cores that already own it, renders
the exact D71 section 8 stimulus bytes, and publishes one immutable manifest.

Nothing here selects Units, selects channels, decides session size, or authors
stimulus text.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Callable, Mapping

from ..artifact_store import ArtifactStore
from ..assessment_evidence import validate_unit_evidence
from ..assessment_identity import cognitive_stimulus_ref
from ..contracts import (
    ANKI_NOTE_TYPE_NAME,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    NOTE_FIELDS,
)
from ..models import VocabUnit
from ..session import create_session_manifest, persist_session_manifest
from .errors import RuntimeSessionCreationError
from .normalize import (
    ANKI_SEAM,
    ARTIFACT_SEAM,
    IDENTITY_SEAM,
    MANIFEST_SEAM,
    UNIT_EVIDENCE_SEAM,
    normalized,
)
from .session_plan import (
    STIMULUS_FIELDS_BY_CHANNEL,
    SessionPlan,
    validate_session_plan,
)
from .targets import read_registry, resolve_note_id


def utc_clock() -> str:
    """Return one aware UTC instant as normalized ISO-8601 with +00:00.

    The machine-local timezone is never consulted, and the project local-day
    helper used by FORGE is deliberately not reused: a session manifest is
    identified by an instant, not by a human calendar day.
    """
    return datetime.now(timezone.utc).isoformat()


def render_stimulus_bytes(channel: str, stimulus: Mapping[str, str]) -> bytes:
    """Render the exact D71 section 8 learner-facing artifact bytes.

    Two field values joined by one blank line, UTF-8, with no BOM, no label, no
    prefix, no suffix, and no added final newline. Nothing is stripped,
    normalized, repaired, or case folded.
    """
    if channel not in STIMULUS_FIELDS_BY_CHANNEL:
        raise RuntimeSessionCreationError(
            f"channel {channel!r} has no Wave C v1 rendering rule"
        )
    first, second = STIMULUS_FIELDS_BY_CHANNEL[channel]
    return (
        stimulus[first].encode("utf-8")
        + b"\n\n"
        + stimulus[second].encode("utf-8")
    )


def stimulus_artifact_ref(payload: bytes) -> str:
    """Derive the artifact ref purely, before any durable write."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """One fully derived manifest item plus its not-yet-stored artifact bytes."""

    item: Mapping[str, object]
    stimulus_bytes: bytes
    expected_ref: str


@dataclass(frozen=True, slots=True)
class SessionCreationResult:
    """The outcome of one published Wave C session."""

    session_id: str
    created_at: str
    item_count: int


def _unit_from_note(note: object, unit_key: str, note_id: int) -> VocabUnit:
    """Build the existing VocabUnit from exactly the persisted note fields.

    The returned payload must prove it is the note that was requested: a
    notesInfo response carrying a different noteId is a different Unit, and
    accepting it would silently assess the wrong note.
    """
    if not isinstance(note, dict):
        raise RuntimeSessionCreationError(
            f"note payload for {unit_key} is not a mapping"
        )
    returned_id = note.get("noteId")
    if type(returned_id) is not int or returned_id != note_id:
        raise RuntimeSessionCreationError(
            f"notes_info for {unit_key} returned noteId {returned_id!r}, "
            f"not the requested {note_id}"
        )
    if note.get("modelName") != ANKI_NOTE_TYPE_NAME:
        raise RuntimeSessionCreationError(
            f"note for {unit_key} is not a {ANKI_NOTE_TYPE_NAME} note"
        )
    fields = note.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(NOTE_FIELDS):
        raise RuntimeSessionCreationError(
            f"note for {unit_key} does not carry the exact note field set"
        )

    values: dict[str, str] = {}
    for name in NOTE_FIELDS:
        entry = fields[name]
        if not isinstance(entry, dict) or "value" not in entry:
            raise RuntimeSessionCreationError(
                f"note field {name} for {unit_key} is malformed"
            )
        value = entry["value"]
        if type(value) is not str:
            raise RuntimeSessionCreationError(
                f"note field {name} for {unit_key} is not a string"
            )
        values[name] = value

    if values["unit_key"] != unit_key:
        raise RuntimeSessionCreationError(
            f"resolved note does not carry unit_key {unit_key}"
        )
    return VocabUnit(**values)


def _validated_units(
    plan: SessionPlan, anki: object
) -> dict[str, object]:
    """Resolve and validate every requested Unit through the evidence boundary.

    The registry snapshot is the global validity authority, but RegistryEntry
    carries no channel information, so enabled-channel authority comes from
    ValidatedUnitEvidence rather than from the registry.
    """
    registry = read_registry(anki)
    known = {entry.unit_key for entry in registry}

    validated: dict[str, object] = {}
    for index, item in enumerate(plan.items):
        if item.unit_key not in known:
            raise RuntimeSessionCreationError(
                f"items[{index}].unit_key {item.unit_key} is not in the active "
                "registry"
            )
        if item.unit_key in validated:
            evidence = validated[item.unit_key]
        else:
            note_id = resolve_note_id(item.unit_key, anki)
            with normalized(
                RuntimeSessionCreationError,
                f"note {note_id} could not be read",
                catching=ANKI_SEAM,
            ):
                notes = anki.notes_info([note_id])  # type: ignore[attr-defined]
            if not isinstance(notes, list) or len(notes) != 1:
                raise RuntimeSessionCreationError(
                    f"note lookup for {item.unit_key} did not return one note"
                )
            unit = _unit_from_note(notes[0], item.unit_key, note_id)
            with normalized(
                RuntimeSessionCreationError,
                f"Unit {item.unit_key} did not pass the evidence boundary",
                catching=UNIT_EVIDENCE_SEAM,
            ):
                evidence = validate_unit_evidence(unit)
            validated[item.unit_key] = evidence

        if item.channel not in evidence.enabled_channels:  # type: ignore[attr-defined]
            raise RuntimeSessionCreationError(
                f"items[{index}] requests channel {item.channel} but "
                f"{item.unit_key} enables "
                f"{list(evidence.enabled_channels)}"  # type: ignore[attr-defined]
            )
    return validated


def derive_candidates(plan: SessionPlan) -> tuple[CandidateItem, ...]:
    """Derive every manifest item and artifact ref purely, before any write."""
    candidates: list[CandidateItem] = []
    for ordinal, item in enumerate(plan.items):
        task_kind = ASSESSMENT_TASK_KIND_BY_CHANNEL[item.channel]
        stimulus = dict(item.stimulus)
        with normalized(
            RuntimeSessionCreationError,
            f"items[{ordinal}] cognitive identity could not be derived",
            catching=IDENTITY_SEAM,
        ):
            presented_ref = cognitive_stimulus_ref(
                unit_key=item.unit_key,
                channel=item.channel,
                task_kind=task_kind,
                stimulus=stimulus,
            )
        payload = render_stimulus_bytes(item.channel, item.stimulus)
        expected_ref = stimulus_artifact_ref(payload)
        candidates.append(
            CandidateItem(
                item=MappingProxyType(
                    {
                        "item_ordinal": ordinal,
                        "unit_key": item.unit_key,
                        "channel": item.channel,
                        "task_kind": task_kind,
                        "stimulus": stimulus,
                        "presented_stimulus_ref": presented_ref,
                        "stimulus_artifact_ref": expected_ref,
                    }
                ),
                stimulus_bytes=payload,
                expected_ref=expected_ref,
            )
        )
    return tuple(candidates)


def create_session(
    plan: SessionPlan,
    *,
    anki: object,
    artifact_store: ArtifactStore,
    session_root: object,
    clock: Callable[[], str] = utc_clock,
) -> SessionCreationResult:
    """Compose and publish one Wave C session, failing closed as a whole.

    Every pure validation and derivation completes before the first durable
    write. A single invalid item therefore yields zero stimulus artifacts and no
    manifest.
    """
    if not isinstance(artifact_store, ArtifactStore):
        raise RuntimeSessionCreationError("artifact_store must be an ArtifactStore")

    # Re-validate first, and derive from the detached snapshot only, so a
    # directly constructed SessionPlan cannot bypass the D71 plan rules and a
    # caller mutating its own stimulus afterwards cannot change what is stored.
    plan = validate_session_plan(plan)
    _validated_units(plan, anki)
    candidates = derive_candidates(plan)

    created_at = clock()
    with normalized(
        RuntimeSessionCreationError,
        "session manifest could not be composed",
        catching=MANIFEST_SEAM,
    ):
        manifest = create_session_manifest(
            created_at=created_at,
            items=[dict(candidate.item) for candidate in candidates],
        )

    for candidate in candidates:
        with normalized(
            RuntimeSessionCreationError,
            "stimulus artifact could not be stored",
            catching=ARTIFACT_SEAM,
        ):
            actual_ref = artifact_store.put(candidate.stimulus_bytes)
        if actual_ref != candidate.expected_ref:
            raise RuntimeSessionCreationError(
                "stored stimulus artifact ref does not match the derived ref"
            )

    with normalized(
        RuntimeSessionCreationError,
        "session manifest could not be persisted",
        catching=MANIFEST_SEAM,
    ):
        persist_session_manifest(session_root, manifest)

    return SessionCreationResult(
        session_id=manifest.session_id,
        created_at=created_at,
        item_count=len(candidates),
    )
