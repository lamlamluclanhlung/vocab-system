"""Operational note-id resolution for reconcile, frozen by D70 section 16.

The global registry authority remains vocab.corpus.read_registry_snapshot. This
module adds only the note-id binding that RegistryEntry deliberately omits, and
it never re-implements NOTE_FIELDS or VocabUnit validation.

Discovery is global on purpose. The complete registry snapshot runs before any
target is resolved, and every selected target is resolved before the first
lifecycle write, so a duplicate unit_key anywhere in the profile refuses the
whole command rather than producing a partial run.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..corpus import RegistryEntry, read_registry_snapshot
from ..forge.build import build_unit_key_query
from .errors import RuntimeTargetDiscoveryError
from .normalize import CORPUS_SEAM_SCAN, normalized


@dataclass(frozen=True, slots=True)
class ReconcileTarget:
    """One registry entry bound to exactly one Anki note."""

    unit_key: str
    lemma: str
    note_id: int


def read_registry(anki: object) -> tuple[RegistryEntry, ...]:
    """Validate the entire active VocabularyUnit registry, failing closed."""
    with normalized(
        RuntimeTargetDiscoveryError,
        "profile registry could not be read",
        catching=CORPUS_SEAM_SCAN,
    ):
        return read_registry_snapshot(anki)


def resolve_note_id(unit_key: str, anki: object) -> int:
    """Bind one unit_key to exactly one Anki note, failing closed otherwise.

    Extracted unchanged from the Wave B resolver so Wave C can reuse the exact
    binding rules rather than writing a second unit_key query.
    """
    entry_key = unit_key
    query = build_unit_key_query(entry_key)
    with normalized(
        RuntimeTargetDiscoveryError,
        f"note lookup failed for {entry_key}",
        catching=CORPUS_SEAM_SCAN,
    ):
        found = anki.find_notes(query)  # type: ignore[attr-defined]

    if not isinstance(found, list):
        raise RuntimeTargetDiscoveryError(
            f"note lookup for {entry_key} did not return a list"
        )
    for candidate in found:
        if type(candidate) is not int:
            raise RuntimeTargetDiscoveryError(
                f"note lookup for {entry_key} returned a non-integer id"
            )
    if len(set(found)) != len(found):
        raise RuntimeTargetDiscoveryError(
            f"note lookup for {entry_key} returned duplicate ids"
        )
    if not found:
        raise RuntimeTargetDiscoveryError(
            f"no note binds unit_key {entry_key}"
        )
    if len(found) > 1:
        raise RuntimeTargetDiscoveryError(
            f"unit_key {entry_key} is ambiguous across notes {sorted(found)}"
        )
    return found[0]


def _resolve_note_id(entry: RegistryEntry, anki: object) -> int:
    """Wave B call site, preserved exactly."""
    return resolve_note_id(entry.unit_key, anki)


def resolve_targets(
    anki: object,
    *,
    unit_key: str | None = None,
) -> tuple[ReconcileTarget, ...]:
    """Enumerate the whole registry, then bind every selected entry to a note.

    A single-unit selection still performs the complete global enumeration
    first, so an inconsistency anywhere in the profile refuses the command
    rather than being stepped around.
    """
    registry = read_registry(anki)

    if unit_key is None:
        selected = registry
    else:
        selected = tuple(entry for entry in registry if entry.unit_key == unit_key)
        if not selected:
            raise RuntimeTargetDiscoveryError(
                f"unit_key {unit_key} is not in the active registry"
            )

    targets = [
        ReconcileTarget(
            unit_key=entry.unit_key,
            lemma=entry.lemma,
            note_id=_resolve_note_id(entry, anki),
        )
        for entry in selected
    ]
    targets.sort(key=lambda target: target.unit_key)
    return tuple(targets)
