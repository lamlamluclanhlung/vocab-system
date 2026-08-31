"""Seam-specific normalization of operational failures (D70 section 18).

Normalization is deliberately per seam. There is no global taxonomy, and in
particular ValueError is never treated as operational across arbitrary blocks:
several core errors happen to be ValueError subclasses, but that does not make
every ValueError an operational refusal, and a defect that raises a bare
ValueError must surface rather than becoming exit 1.

Each context manager below wraps one narrow operation and catches only the
exception family that operation can legitimately raise. Anything else, and
every programming defect, is allowed to escape with its traceback.

The deployment journal's acquisition and strict-read seam is not here. It lives inside
vocab/runtime/eventlog_authority.py, wrapped around the exact acquisition and read_strict calls,
because that module is the only one permitted to import the journal class and
its structural shape is frozen by the D70 section 7 invariant.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..anki import AnkiConnectError
from ..artifact_store import ArtifactStoreError
from ..assessment_evidence import AssessmentEvidenceError
from ..assessment_identity import AssessmentIdentityError
from ..assessment_planning import AssessmentPlanningError
from ..assessment_producer import AssessmentProducerError
from ..capture_ledger import CaptureLedgerError
from ..disposition_ledger import DispositionLedgerError
from ..corpus import CorpusScanError
from ..exposure import ExposureLedgerError
from ..human_review import HumanReviewError
from ..presence_evidence import PresenceEvidenceError
from ..review_materialization import T11MaterializationError
from ..semantic_evidence import SemanticEvidenceError
from ..semantic_request import SemanticRequestError
from ..semantic_response import SemanticResponseError
from ..session import SessionManifestError
from ..reconcile import (
    ReconcileDecisionError,
    ReconcileMaterializationError,
    ReconcileObservationError,
    ReconcileReactivationError,
)
from ..transcription_ledger import TranscriptionLedgerError
from .errors import VocabRuntimeError


ANKI_SEAM: tuple[type[BaseException], ...] = (AnkiConnectError,)
CORPUS_SEAM: tuple[type[BaseException], ...] = (CorpusScanError, AnkiConnectError)
ARTIFACT_SEAM: tuple[type[BaseException], ...] = (ArtifactStoreError,)
# validate_t12_histories reads all three T12 ledgers, so the disposition family
# belongs here too: a torn disposition history is an operational refusal.
LEDGER_SEAM: tuple[type[BaseException], ...] = (
    CaptureLedgerError,
    ExposureLedgerError,
    DispositionLedgerError,
    TranscriptionLedgerError,
)
FILESYSTEM_SEAM: tuple[type[BaseException], ...] = (OSError,)

# T9 raises a named hierarchy. ReconcileDecisionError subclasses ValueError, but
# it is a named operational class and is caught as that exact type; this is not
# a licence to treat every ValueError as operational.
RECONCILE_SEAM: tuple[type[BaseException], ...] = (
    ReconcileObservationError,
    ReconcileDecisionError,
    ReconcileMaterializationError,
    ReconcileReactivationError,
    AnkiConnectError,
)

# CorpusScanError is the root of every T10 operational failure, including the
# registry, snapshot, count, encounter, history, and emission families.
CORPUS_SEAM_SCAN: tuple[type[BaseException], ...] = (CorpusScanError, AnkiConnectError)

# Wave C seams. Each names the exact family its own operation raises. Several
# are ValueError subclasses, which is why they are named individually rather
# than caught as ValueError: a defect raising a bare ValueError must surface.
ATTEMPT_SEAM: tuple[type[BaseException], ...] = (
    ExposureLedgerError,
    CaptureLedgerError,
    DispositionLedgerError,
    ArtifactStoreError,
    SessionManifestError,
)
EVIDENCE_SEAM: tuple[type[BaseException], ...] = (
    AssessmentEvidenceError,
    ExposureLedgerError,
    CaptureLedgerError,
    DispositionLedgerError,
    TranscriptionLedgerError,
    ArtifactStoreError,
    SessionManifestError,
)

# emit_planned_judge owns producer preflight, idempotency, and conflict
# detection; a conflicting rerun is an operational refusal, not a defect.
PRODUCER_SEAM: tuple[type[BaseException], ...] = (
    AssessmentProducerError,
    AssessmentEvidenceError,
    ExposureLedgerError,
    CaptureLedgerError,
    DispositionLedgerError,
    ArtifactStoreError,
)
# evaluate_presence_gate may legitimately reject the attempt-to-Unit binding.
# read_transcription_ledger raises only its own family: it never reads the
# disposition ledger, so the broader ledger seam is not authorized here.
TRANSCRIPTION_SEAM: tuple[type[BaseException], ...] = (TranscriptionLedgerError,)

# load_session_manifest reads no ledger at all.
MANIFEST_SEAM: tuple[type[BaseException], ...] = (SessionManifestError,)

# cognitive_stimulus_ref and assessment_attempt_id are pure identity derivations.
IDENTITY_SEAM: tuple[type[BaseException], ...] = (AssessmentIdentityError,)

# validate_unit_evidence raises exactly its own family. TypeError from it would
# be a defect in the value we constructed, not an operational condition.
UNIT_EVIDENCE_SEAM: tuple[type[BaseException], ...] = (AssessmentEvidenceError,)

PRESENCE_SEAM: tuple[type[BaseException], ...] = (
    PresenceEvidenceError,
    AssessmentEvidenceError,
)
SEMANTIC_SEAM: tuple[type[BaseException], ...] = (
    AssessmentEvidenceError,
    PresenceEvidenceError,
    SemanticRequestError,
    SemanticResponseError,
    HumanReviewError,
    T11MaterializationError,
    SemanticEvidenceError,
)
PLANNING_SEAM: tuple[type[BaseException], ...] = (
    AssessmentPlanningError,
    AssessmentEvidenceError,
)


@contextmanager
def normalized(
    error_type: type[VocabRuntimeError],
    message: str,
    *,
    catching: tuple[type[BaseException], ...],
) -> Iterator[None]:
    """Wrap one narrow seam, converting only its own failure family."""
    try:
        yield
    except VocabRuntimeError:
        raise
    except catching as exc:
        raise error_type(f"{message}: {type(exc).__name__}: {exc}") from exc
