"""Public request and result types for the T6 Forge pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None

PRODUCER_VERSION = "t6-v1"

FORGE_REJECTION_OUTCOMES = (
    "VALIDATOR_REJECTED",
    "JUSTIFICATION_MISSING",
    "DUPLICATE",
    "HUMAN_DECLINED",
)

FORGE_OPERATION_OUTCOMES = (
    "COMMIT_INTENT",
    "ANKI_COMMIT_UNCERTAIN",
    "INTENT_ABANDONED",
)

FORGE_ABORT_REASONS = (
    "REQUEST_INVALID",
    "GENERATION_FAILED",
    "SCHEMA_INVALID",
    "IDENTITY_INVALID",
    "EVENTLOG_UNAVAILABLE",
    "ANKI_READ_FAILED",
    "ATTEMPT_ID_INVALID",
)

FORGE_RESULT_ONLY_REASONS = (
    "PENDING_INTENT",
    "PENDING_INTENT_AMBIGUOUS",
    "ACCEPTANCE_UNWRITABLE",
)


class ForgeStatus(str, Enum):
    CREATED = "CREATED"
    REJECTED = "REJECTED"
    ABORTED = "ABORTED"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    COMMIT_UNCERTAIN = "COMMIT_UNCERTAIN"


@dataclass(frozen=True, slots=True)
class ForgeRequest:
    source_ref: str
    source_sentence: str
    learner_note: str = ""


@dataclass(frozen=True, slots=True)
class GenerationMetadata:
    model_id: str
    model_version: str
    prompt_version: str
    prompt_sha256: str
    generation_config: Mapping[str, JSONScalar]


@dataclass(frozen=True, slots=True)
class ForgePreview:
    unit_key: str
    lemma: str
    unit_type: str
    register: str
    definition_en: str
    source_ref: str
    source_sentence: str
    targets: tuple[str, ...]
    states: tuple[tuple[str, str], ...]
    target_justification: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    confirmed: bool
    actor_id: str


@dataclass(frozen=True, slots=True)
class ForgeResult:
    status: ForgeStatus
    unit_key: str = ""
    forge_attempt_id: str = ""
    note_id: int | None = None
    outcome: str = ""
    violations: tuple[str, ...] = ()
    ambiguous_note_ids: tuple[int, ...] = ()


class RepairStatus(str, Enum):
    REPAIRED = "REPAIRED"
    NO_NOTE = "NO_NOTE"
    AMBIGUOUS = "AMBIGUOUS"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class RepairResult:
    status: RepairStatus
    forge_attempt_id: str = ""
    unit_key: str = ""
    note_id: int | None = None
    outcome: str = ""
    ambiguous_note_ids: tuple[int, ...] = ()
