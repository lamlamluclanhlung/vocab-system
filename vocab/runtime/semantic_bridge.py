"""Wave C T11 transport and final T12 assessment, frozen by D71 sections 15-20.

External files are transport copies only. Before an artifact participates in
final semantic evidence the deployment holds its canonical ArtifactStore copy,
and chaining is always explicit: there is no current request, current proposal,
current review, or artifact-directory discovery.

This module ends at evidence emission. It never touches lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

from ..artifact_store import ArtifactStore
from ..assessment_evidence import (
    load_validated_attempt_evidence,
    load_validated_disposition_evidence,
    validate_unit_evidence,
)
from ..assessment_identity import assessment_attempt_id
from ..assessment_planning import plan_policy_judge, plan_text_judge
from ..assessment_producer import emit_planned_judge
from ..human_review import build_human_review, serialize_human_review
from ..presence_evidence import evaluate_presence_gate
from ..semantic_evidence import bind_t11_semantic_evidence
from ..semantic_request import (
    build_semantic_request,
    prepare_semantic_request_submission,
)
from ..semantic_response import (
    canonical_semantic_proposal_bytes,
    import_semantic_response,
)
from .assessment_session import _unit_from_note
from .attempt_runner import WAVE_C_CHANNELS
from .errors import RuntimeAssessmentError, RuntimeSemanticBridgeError
from .layout import DeploymentLayout
from .normalize import (
    ANKI_SEAM,
    ARTIFACT_SEAM,
    HUMAN_REVIEW_SEAM,
    IDENTITY_SEAM,
    MANIFEST_SEAM,
    PLANNING_SEAM,
    PRESENCE_SEAM,
    PRODUCER_SEAM,
    SEMANTIC_BINDING_SEAM,
    SEMANTIC_PROPOSAL_SEAM,
    SEMANTIC_REQUEST_SEAM,
    TEXT_EVIDENCE_SEAM,
    UNIT_EVIDENCE_SEAM,
    normalized,
)
from .session_plan import STIMULUS_FIELDS_BY_CHANNEL
from .targets import read_registry, resolve_note_id

TASK_CONTENT_RESPONSE_FIELD = "learner_response"


@dataclass(frozen=True, slots=True)
class SemanticExportResult:
    """The durable outcome of one semantic request export."""

    attempt_id: str
    request_ref: str
    request_digest: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    """The outcome of one final JUDGE emission."""

    attempt_id: str
    unit_key: str
    channel: str
    path: str
    appended: int


def _manifest_item(layout: DeploymentLayout, session_id: str, item_ordinal: int):
    from ..artifact_json import strict_json_loads
    from ..session import load_session_manifest

    with normalized(
        RuntimeAssessmentError,
        "session manifest could not be loaded",
        catching=MANIFEST_SEAM,
    ):
        manifest = load_session_manifest(layout.session_root, session_id)
    decoded = strict_json_loads(manifest.canonical_bytes)
    matching = [
        item
        for item in decoded["items"]
        if type(item) is dict and item.get("item_ordinal") == item_ordinal
    ]
    if len(matching) != 1:
        raise RuntimeAssessmentError(
            f"session manifest has no unique item_ordinal {item_ordinal}"
        )
    item = matching[0]
    if item["channel"] not in WAVE_C_CHANNELS:
        raise RuntimeAssessmentError("Wave C v1 operates only R and W")
    with normalized(
        RuntimeAssessmentError,
        "attempt identity could not be derived",
        catching=IDENTITY_SEAM,
    ):
        attempt_id = assessment_attempt_id(
            session_id=manifest.session_id,
            item_ordinal=item_ordinal,
            unit_key=item["unit_key"],
            channel=item["channel"],
            presented_stimulus_ref=item["presented_stimulus_ref"],
        )
    return manifest, item, attempt_id


def _validated_unit(unit_key: str, anki: object):
    """Resolve and validate one Unit through the existing evidence boundary."""
    registry = read_registry(anki)
    if unit_key not in {entry.unit_key for entry in registry}:
        raise RuntimeAssessmentError(
            f"unit_key {unit_key} is not in the active registry"
        )
    note_id = resolve_note_id(unit_key, anki)
    with normalized(
        RuntimeAssessmentError,
        f"note {note_id} could not be read",
        catching=ANKI_SEAM,
    ):
        notes = anki.notes_info([note_id])  # type: ignore[attr-defined]
    if not isinstance(notes, list) or len(notes) != 1:
        raise RuntimeAssessmentError(
            f"note lookup for {unit_key} did not return one note"
        )
    with normalized(
        RuntimeAssessmentError,
        f"Unit {unit_key} did not pass the evidence boundary",
        catching=UNIT_EVIDENCE_SEAM,
    ):
        return validate_unit_evidence(
            _unit_from_note(notes[0], unit_key, note_id)
        )


def _load_attempt(layout: DeploymentLayout, artifact_store: ArtifactStore, attempt_id: str):
    with normalized(
        RuntimeAssessmentError,
        "captured attempt could not be reconstructed",
        catching=TEXT_EVIDENCE_SEAM,
    ):
        return load_validated_attempt_evidence(
            exposure_path=layout.exposure_path,
            capture_path=layout.capture_path,
            disposition_path=layout.disposition_path,
            artifact_store=artifact_store,
            session_root=layout.session_root,
            attempt_id=attempt_id,
        )


def _task_content(item: Mapping[str, object], attempt) -> dict[str, str]:
    channel = cast(str, item["channel"])
    stimulus = item["stimulus"]
    content = {
        field: cast(str, stimulus[field])  # type: ignore[index]
        for field in STIMULUS_FIELDS_BY_CHANNEL[channel]
    }
    try:
        content[TASK_CONTENT_RESPONSE_FIELD] = attempt.response_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeSemanticBridgeError(
            "captured response is not valid UTF-8"
        ) from exc
    return content


def _expected_request(item, attempt, unit) -> tuple[bytes, str]:
    """Rebuild the exact canonical semantic request for this attempt.

    Export and final assessment share this one construction, so the request a
    proposal answers is always the request this attempt actually determines.
    """
    with normalized(
        RuntimeSemanticBridgeError,
        "semantic request could not be built",
        catching=SEMANTIC_REQUEST_SEAM,
    ):
        request = build_semantic_request(
            unit_key=unit.unit_key,
            lemma=unit.lemma,
            unit_type=unit.unit_type,
            definition_en=unit.definition_en,
            channel=cast(str, item["channel"]),
            task_content=_task_content(item, attempt),
        )
        return prepare_semantic_request_submission(request)


def _presence_for_w(attempt, unit):
    with normalized(
        RuntimeSemanticBridgeError,
        "presence gate could not be evaluated",
        catching=PRESENCE_SEAM,
    ):
        return evaluate_presence_gate(attempt=attempt, unit=unit)


def export_semantic_request(
    layout: DeploymentLayout,
    *,
    session_id: str,
    item_ordinal: int,
    artifact_store: ArtifactStore,
    anki: object,
) -> SemanticExportResult:
    """Build, canonicalize, and durably store one attempt-bound request."""
    _manifest, item, attempt_id = _manifest_item(layout, session_id, item_ordinal)
    unit = _validated_unit(cast(str, item["unit_key"]), anki)
    attempt = _load_attempt(layout, artifact_store, attempt_id)

    if item["channel"] == "W":
        presence = _presence_for_w(attempt, unit)
        if not presence.target_present:
            raise RuntimeSemanticBridgeError(
                "W target is absent; the deterministic OMITTED path applies and "
                "no semantic request may be created"
            )

    canonical_bytes, request_digest = _expected_request(item, attempt, unit)

    with normalized(
        RuntimeSemanticBridgeError,
        "canonical semantic request could not be stored",
        catching=ARTIFACT_SEAM,
    ):
        request_ref = artifact_store.put(canonical_bytes)
    if request_ref != f"sha256:{request_digest}":
        raise RuntimeSemanticBridgeError(
            "stored semantic request ref does not match its request digest"
        )
    return SemanticExportResult(
        attempt_id=attempt_id,
        request_ref=request_ref,
        request_digest=request_digest,
        canonical_bytes=canonical_bytes,
    )


def _emit(layout, artifact_store, open_event_log, planned, path, attempt_id, item):
    """Acquire the journal only now, after the plan is sealed."""
    with normalized(
        RuntimeAssessmentError,
        "assessment evidence could not be emitted",
        catching=PRODUCER_SEAM,
    ):
        appended = emit_planned_judge(
            event_log=open_event_log(),
            exposure_path=layout.exposure_path,
            capture_path=layout.capture_path,
            disposition_path=layout.disposition_path,
            artifact_store=artifact_store,
            planned=planned,
        )
    return AssessmentResult(
        attempt_id=attempt_id,
        unit_key=cast(str, item["unit_key"]),
        channel=cast(str, item["channel"]),
        path=path,
        appended=len(appended),
    )


def emit_policy_assessment(
    layout: DeploymentLayout,
    *,
    session_id: str,
    item_ordinal: int,
    artifact_store: ArtifactStore,
    anki: object,
    open_event_log: object,
) -> AssessmentResult:
    """Emit the JUDGE for one attempt ended by a durable D67 disposition."""
    _manifest, item, attempt_id = _manifest_item(layout, session_id, item_ordinal)
    unit = _validated_unit(cast(str, item["unit_key"]), anki)
    with normalized(
        RuntimeAssessmentError,
        "disposition evidence could not be reconstructed",
        catching=TEXT_EVIDENCE_SEAM,
    ):
        disposition = load_validated_disposition_evidence(
            exposure_path=layout.exposure_path,
            capture_path=layout.capture_path,
            disposition_path=layout.disposition_path,
            artifact_store=artifact_store,
            session_root=layout.session_root,
            attempt_id=attempt_id,
        )
    with normalized(
        RuntimeAssessmentError,
        "policy judge could not be planned",
        catching=PLANNING_SEAM,
    ):
        planned = plan_policy_judge(disposition=disposition, unit=unit)
    return _emit(
        layout, artifact_store, open_event_log, planned, "policy", attempt_id, item
    )


def emit_omitted_assessment(
    layout: DeploymentLayout,
    *,
    session_id: str,
    item_ordinal: int,
    artifact_store: ArtifactStore,
    anki: object,
    open_event_log: object,
) -> AssessmentResult:
    """Emit the deterministic W OMITTED JUDGE when the target is absent."""
    _manifest, item, attempt_id = _manifest_item(layout, session_id, item_ordinal)
    if item["channel"] != "W":
        raise RuntimeAssessmentError("the OMITTED path applies only to W")
    unit = _validated_unit(cast(str, item["unit_key"]), anki)
    attempt = _load_attempt(layout, artifact_store, attempt_id)
    presence = _presence_for_w(attempt, unit)
    if presence.target_present:
        raise RuntimeAssessmentError(
            "W target is present; the semantic path is required"
        )
    with normalized(
        RuntimeAssessmentError,
        "omitted judge could not be planned",
        catching=PLANNING_SEAM,
    ):
        planned = plan_text_judge(attempt=attempt, unit=unit, presence=presence)
    return _emit(
        layout, artifact_store, open_event_log, planned, "omitted", attempt_id, item
    )


def emit_semantic_assessment(
    layout: DeploymentLayout,
    *,
    session_id: str,
    item_ordinal: int,
    request_ref: str,
    proposal_bytes: bytes,
    assessor_id: str,
    assessor_version: str,
    reviewer_id: str,
    reviewer_version: int,
    decision: str,
    artifact_store: ArtifactStore,
    anki: object,
    open_event_log: object,
) -> AssessmentResult:
    """Bind the exact T11 chain and emit the semantic JUDGE, all under one lock."""
    _manifest, item, attempt_id = _manifest_item(layout, session_id, item_ordinal)
    unit = _validated_unit(cast(str, item["unit_key"]), anki)
    attempt = _load_attempt(layout, artifact_store, attempt_id)

    presence = None
    if item["channel"] == "W":
        presence = _presence_for_w(attempt, unit)
        if not presence.target_present:
            raise RuntimeSemanticBridgeError(
                "W target is absent; the semantic path is forbidden"
            )

    with normalized(
        RuntimeSemanticBridgeError,
        "canonical semantic request could not be read",
        catching=ARTIFACT_SEAM,
    ):
        request_raw = artifact_store.read(request_ref)

    # D71 section 16: the request must be proven canonical and attempt-bound
    # before any proposal or review is imported or stored, so a foreign or
    # non-canonical request can never leave a new artifact behind.
    expected_bytes, expected_digest = _expected_request(item, attempt, unit)
    if request_raw != expected_bytes:
        raise RuntimeSemanticBridgeError(
            "stored semantic request is not the canonical request this attempt "
            "determines"
        )
    if request_ref != f"sha256:{expected_digest}":
        raise RuntimeSemanticBridgeError(
            "request_ref does not match this attempt's request digest"
        )

    with normalized(
        RuntimeSemanticBridgeError,
        "semantic proposal could not be imported",
        catching=SEMANTIC_PROPOSAL_SEAM,
    ):
        from ..artifact_json import strict_json_loads

        imported = import_semantic_response(
            proposal_bytes,
            request=strict_json_loads(expected_bytes),
            assessor_id=assessor_id,
            assessor_version=assessor_version,
        )
        proposal_canonical = canonical_semantic_proposal_bytes(imported)

    with normalized(
        RuntimeSemanticBridgeError,
        "canonical semantic proposal could not be stored",
        catching=ARTIFACT_SEAM,
    ):
        proposal_ref = artifact_store.put(proposal_canonical)
    if proposal_ref != f"sha256:{imported.response_digest}":
        raise RuntimeSemanticBridgeError(
            "stored proposal ref does not match its response digest"
        )

    with normalized(
        RuntimeSemanticBridgeError,
        "human review could not be built",
        catching=HUMAN_REVIEW_SEAM,
    ):
        review = build_human_review(
            imported_proposal=imported,
            reviewer_id=reviewer_id,
            reviewer_version=reviewer_version,
            decision=decision,
        )
        review_canonical = serialize_human_review(review, imported_proposal=imported)

    with normalized(
        RuntimeSemanticBridgeError,
        "canonical human review could not be stored",
        catching=ARTIFACT_SEAM,
    ):
        artifact_store.put(review_canonical)

    with normalized(
        RuntimeSemanticBridgeError,
        "semantic evidence could not be bound",
        catching=SEMANTIC_BINDING_SEAM,
    ):
        bundle = bind_t11_semantic_evidence(
            request_raw=request_raw,
            proposal_raw=proposal_canonical,
            review_raw=review_canonical,
            assessor_id=assessor_id,
            assessor_version=assessor_version,
            attempt=attempt,
            unit=unit,
            presence=presence,
        )

    with normalized(
        RuntimeAssessmentError,
        "semantic judge could not be planned",
        catching=PLANNING_SEAM,
    ):
        planned = plan_text_judge(
            attempt=attempt, unit=unit, semantic=bundle, presence=presence
        )
    return _emit(
        layout, artifact_store, open_event_log, planned, "semantic", attempt_id, item
    )
