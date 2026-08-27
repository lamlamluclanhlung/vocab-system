"""Deterministic construction and immutable preview helpers for Forge."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from ..contracts import CHANNELS, STATE_NEW, UNIQUE_NOTE_FIELD, UNIT_KEY_SEPARATOR
from ..models import VocabUnit
from .request import ForgePreview, ForgeRequest


def build_vocab_unit(
    structured_output: Mapping[str, object],
    request: ForgeRequest,
    *,
    created_on: date,
) -> VocabUnit:
    """Build a VocabUnit without normalizing, repairing, or inventing data."""
    if type(created_on) is not date:
        raise TypeError("created_on must be a date")

    lemma_slug = structured_output["lemma_slug"]
    sense_slug = structured_output["sense_slug"]
    if not isinstance(lemma_slug, str) or not isinstance(sense_slug, str):
        raise TypeError("strict output identity fields must be strings")

    targets = {
        channel: "1" if structured_output[f"target_{channel}"] is True else ""
        for channel in CHANNELS
    }
    states = {
        channel: STATE_NEW if targets[channel] == "1" else ""
        for channel in CHANNELS
    }

    return VocabUnit(
        unit_key=lemma_slug + UNIT_KEY_SEPARATOR + sense_slug,
        lemma=structured_output["lemma"],
        lemma_slug=lemma_slug,
        sense_slug=sense_slug,
        unit_type=structured_output["unit_type"],
        Target_R=targets["R"],
        Target_L=targets["L"],
        Target_W=targets["W"],
        Target_S=targets["S"],
        register=structured_output["register"],
        definition_en=structured_output["definition_en"],
        source_ref=request.source_ref,
        source_sentence=request.source_sentence,
        Ctx_1="",
        Ctx_2="",
        Ctx_3="",
        Ctx_4="",
        Ctx_5="",
        audio_1="",
        audio_2="",
        audio_3="",
        VisualCue="",
        state_R=states["R"],
        state_L=states["L"],
        state_W=states["W"],
        state_S=states["S"],
        freq_band="",
        created=created_on.isoformat(),
        graduated="",
    )


def build_unit_key_query(unit_key: str) -> str:
    """Build Anki's exact-match field query for one trusted unit key."""
    return f"{UNIQUE_NOTE_FIELD}:{unit_key}"


def build_preview(
    unit: VocabUnit,
    target_justification: Mapping[str, str],
) -> ForgePreview:
    """Copy a candidate into a frozen scalar-and-tuple-only preview."""
    targets = tuple(
        channel
        for channel in CHANNELS
        if getattr(unit, f"Target_{channel}") == "1"
    )
    states = tuple(
        (channel, getattr(unit, f"state_{channel}")) for channel in targets
    )
    justifications = tuple(
        (channel, target_justification[channel])
        for channel in ("W", "S")
        if channel in target_justification
    )
    return ForgePreview(
        unit_key=unit.unit_key,
        lemma=unit.lemma,
        unit_type=unit.unit_type,
        register=unit.register,
        definition_en=unit.definition_en,
        source_ref=unit.source_ref,
        source_sentence=unit.source_sentence,
        targets=targets,
        states=states,
        target_justification=justifications,
    )
