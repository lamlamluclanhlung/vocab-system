"""Pure T11 semantic-proposal import, binding, and identity helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import cast

from .artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from .contracts import (
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_PASS,
)
from .models import T11AssessmentResult
from .semantic_request import (
    SemanticRequestError,
    import_semantic_request,
    semantic_request_digest,
)
from .validators import validate_t11_assessment_result


SEMANTIC_RESPONSE_ARTIFACT = "vocab.t11.semantic-response"
SEMANTIC_RESPONSE_VERSION = 1

SEMANTIC_RESPONSE_OUTCOMES = (
    ASSESSMENT_OUTCOME_PASS,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_ABSTAIN,
)
SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES = (
    "off_topic",
    "insufficient_lexical_evidence",
    "response_unintelligible",
    "semantic_uncertainty",
)
ASSESSOR_VERSION_UNAVAILABLE_FROM_UI = "version-unavailable-from-ui"

_PROPOSAL_FIELDS = frozenset(
    (
        "artifact",
        "v",
        "request_digest",
        "outcome",
        "failure_code",
        "reason_code",
        "semantic_rationale",
    )
)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


class SemanticResponseError(ValueError):
    """Raised when a T11 semantic proposal is not exact and request-bound."""


class _FrozenDict(dict[str, object]):
    """A copied artifact/facts mapping that rejects in-place mutation."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("imported semantic proposal data is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


@dataclass(frozen=True, slots=True)
class ImportedSemanticProposal:
    """One accepted pure import with proposal, result, and provenance attached."""

    proposal: dict[str, object]
    assessment_result: T11AssessmentResult
    assessor_id: str
    assessor_version: str
    request_digest: str
    response_digest: str
    semantic_judge_facts: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal", _FrozenDict(self.proposal))
        object.__setattr__(
            self,
            "semantic_judge_facts",
            _FrozenDict(self.semantic_judge_facts),
        )


def import_semantic_response(
    raw: bytes,
    *,
    request: object,
    assessor_id: str,
    assessor_version: str,
) -> ImportedSemanticProposal:
    """Strictly import one request-bound semantic proposal without I/O."""
    try:
        value = strict_json_loads(raw)
    except ArtifactJSONError as exc:
        raise SemanticResponseError(str(exc)) from None

    proposal = _require_proposal_object(value)
    if set(proposal) != _PROPOSAL_FIELDS:
        raise SemanticResponseError("semantic proposal has the wrong key set")

    artifact = proposal["artifact"]
    if type(artifact) is not str or artifact != SEMANTIC_RESPONSE_ARTIFACT:
        raise SemanticResponseError("artifact discriminator is invalid")

    version = proposal["v"]
    if type(version) is not int or version != SEMANTIC_RESPONSE_VERSION:
        raise SemanticResponseError("artifact version is invalid")

    bound_request_digest = proposal["request_digest"]
    if (
        type(bound_request_digest) is not str
        or _LOWER_SHA256_RE.fullmatch(bound_request_digest) is None
    ):
        raise SemanticResponseError("request_digest is invalid")

    supplied_request_digest = semantic_request_digest(request)
    validated_request = import_semantic_request(canonical_json_bytes(request))
    expected_request_digest = semantic_request_digest(validated_request)
    if supplied_request_digest != expected_request_digest:
        raise SemanticRequestError("request changed during validation")
    if bound_request_digest != expected_request_digest:
        raise SemanticResponseError("request_digest does not bind the supplied request")

    validated_assessor_id = _validated_assessor_metadata(
        assessor_id,
        "assessor_id",
    )
    validated_assessor_version = _validated_assessor_metadata(
        assessor_version,
        "assessor_version",
    )

    outcome = _require_exact_string(proposal["outcome"], "outcome")
    failure_code = _require_exact_string(
        proposal["failure_code"],
        "failure_code",
    )
    reason_code = _require_exact_string(proposal["reason_code"], "reason_code")
    semantic_rationale = _require_exact_string(
        proposal["semantic_rationale"],
        "semantic_rationale",
    )

    if outcome not in SEMANTIC_RESPONSE_OUTCOMES:
        raise SemanticResponseError("outcome is invalid for a semantic proposal")
    if (
        outcome == ASSESSMENT_OUTCOME_ABSTAIN
        and reason_code not in SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES
    ):
        raise SemanticResponseError(
            "reason_code is not owned by the semantic assessor"
        )
    if not semantic_rationale.strip():
        raise SemanticResponseError(
            "semantic_rationale must be a non-whitespace string"
        )
    if _SURROGATE_RE.search(semantic_rationale) is not None:
        raise SemanticResponseError(
            "semantic_rationale contains an unpaired surrogate"
        )

    unit = cast(dict[str, object], validated_request["unit"])
    task = cast(dict[str, object], validated_request["task"])
    assessment_result = T11AssessmentResult(
        unit_key=unit["unit_key"],
        channel=task["channel"],
        outcome=outcome,
        failure_code=failure_code,
        reason_code=reason_code,
    )
    violations = validate_t11_assessment_result(assessment_result)
    if violations:
        raise SemanticResponseError(
            "semantic proposal violates the T11 assessment result contract: "
            + ", ".join(violations)
        )

    validated_proposal = {
        "artifact": artifact,
        "v": version,
        "request_digest": expected_request_digest,
        "outcome": outcome,
        "failure_code": failure_code,
        "reason_code": reason_code,
        "semantic_rationale": semantic_rationale,
    }
    response_digest = canonical_sha256(validated_proposal)

    protocol = cast(dict[str, object], validated_request["protocol"])
    rubric = cast(dict[str, object], validated_request["rubric"])
    prompt = cast(dict[str, object], validated_request["prompt"])
    semantic_judge_facts = {
        "protocol_id": protocol["id"],
        "protocol_version": protocol["version"],
        "rubric_id": rubric["id"],
        "rubric_version": rubric["version"],
        "prompt_id": prompt["id"],
        "prompt_version": prompt["version"],
        "assessor_id": validated_assessor_id,
        "assessor_version": validated_assessor_version,
        "request_digest": expected_request_digest,
        "response_digest": response_digest,
    }

    return ImportedSemanticProposal(
        proposal=validated_proposal,
        assessment_result=assessment_result,
        assessor_id=validated_assessor_id,
        assessor_version=validated_assessor_version,
        request_digest=expected_request_digest,
        response_digest=response_digest,
        semantic_judge_facts=semantic_judge_facts,
    )


def canonical_semantic_proposal_bytes(
    imported: ImportedSemanticProposal,
) -> bytes:
    """Return canonical bytes for an already request-bound proposal import."""
    if not isinstance(imported, ImportedSemanticProposal):
        raise TypeError("imported must be an ImportedSemanticProposal")
    return canonical_json_bytes(imported.proposal)


def _require_proposal_object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise SemanticResponseError("semantic proposal must be an object")
    return value


def _require_exact_string(value: object, name: str) -> str:
    if type(value) is not str:
        raise SemanticResponseError(f"{name} must be a string")
    return value


def _validated_assessor_metadata(value: object, name: str) -> str:
    if type(value) is not str:
        raise SemanticResponseError(f"{name} must be a string")
    if not 1 <= len(value) <= 128:
        raise SemanticResponseError(f"{name} must contain 1..128 code points")
    if value != value.strip():
        raise SemanticResponseError(f"{name} must be strip-stable")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise SemanticResponseError(f"{name} must not contain control characters")
    return value
