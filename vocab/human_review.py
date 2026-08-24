"""Pure T11 human-review construction, import, and proposal binding."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from .artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    strict_json_loads,
)
from .contracts import HUMAN_REVIEW_DECISIONS, SLUG_PATTERN
from .semantic_response import (
    ImportedSemanticProposal,
    canonical_semantic_proposal_bytes,
)


HUMAN_REVIEW_ARTIFACT = "vocab.t11.human-review"
HUMAN_REVIEW_VERSION = 1

_REVIEW_FIELDS = frozenset(
    (
        "artifact",
        "v",
        "response_digest",
        "reviewer_id",
        "reviewer_version",
        "decision",
    )
)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID_RE = re.compile(SLUG_PATTERN)


class HumanReviewError(ValueError):
    """Raised when a T11 human review is invalid or incorrectly bound."""


class _FrozenReview(dict[str, object]):
    """A copied review mapping that rejects in-place mutation."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("imported human review is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


@dataclass(frozen=True, slots=True)
class ImportedHumanReview:
    """One validated review bound to one exact imported semantic proposal."""

    review: dict[str, object]
    response_digest: str
    reviewer_id: str
    reviewer_version: int
    decision: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "review", _FrozenReview(self.review))


def build_human_review(
    *,
    imported_proposal: ImportedSemanticProposal,
    reviewer_id: str,
    reviewer_version: int,
    decision: str,
) -> dict[str, object]:
    """Build one validated review artifact bound to an accepted proposal."""
    expected_response_digest = _independent_response_digest(imported_proposal)
    review = {
        "artifact": HUMAN_REVIEW_ARTIFACT,
        "v": HUMAN_REVIEW_VERSION,
        "response_digest": expected_response_digest,
        "reviewer_id": reviewer_id,
        "reviewer_version": reviewer_version,
        "decision": decision,
    }
    return _validated_human_review(review, expected_response_digest)


def import_human_review(
    raw: bytes,
    *,
    imported_proposal: ImportedSemanticProposal,
) -> ImportedHumanReview:
    """Strictly import a human review bound to an accepted proposal."""
    try:
        value = strict_json_loads(raw)
    except ArtifactJSONError as exc:
        raise HumanReviewError(str(exc)) from None

    expected_response_digest = _independent_response_digest(imported_proposal)
    review = _validated_human_review(value, expected_response_digest)
    return ImportedHumanReview(
        review=review,
        response_digest=review["response_digest"],
        reviewer_id=review["reviewer_id"],
        reviewer_version=review["reviewer_version"],
        decision=review["decision"],
    )


def serialize_human_review(
    review: object,
    *,
    imported_proposal: ImportedSemanticProposal,
) -> bytes:
    """Validate, bind, and canonically serialize one complete review."""
    expected_response_digest = _independent_response_digest(imported_proposal)
    return canonical_json_bytes(
        _validated_human_review(review, expected_response_digest)
    )


def _independent_response_digest(
    imported_proposal: ImportedSemanticProposal,
) -> str:
    proposal_bytes = canonical_semantic_proposal_bytes(imported_proposal)
    expected_response_digest = sha256(proposal_bytes).hexdigest()
    if imported_proposal.response_digest != expected_response_digest:
        raise HumanReviewError(
            "imported proposal response_digest does not match proposal content"
        )
    return expected_response_digest


def _validated_human_review(
    value: object,
    expected_response_digest: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise HumanReviewError("human review must be an object")
    if set(value) != _REVIEW_FIELDS:
        raise HumanReviewError("human review has the wrong key set")

    artifact = value["artifact"]
    if type(artifact) is not str or artifact != HUMAN_REVIEW_ARTIFACT:
        raise HumanReviewError("artifact discriminator is invalid")

    version = value["v"]
    if type(version) is not int or version != HUMAN_REVIEW_VERSION:
        raise HumanReviewError("artifact version is invalid")

    response_digest = value["response_digest"]
    if (
        type(response_digest) is not str
        or _LOWER_SHA256_RE.fullmatch(response_digest) is None
    ):
        raise HumanReviewError("response_digest is invalid")
    if response_digest != expected_response_digest:
        raise HumanReviewError(
            "response_digest does not bind the imported semantic proposal"
        )

    reviewer_id = value["reviewer_id"]
    if (
        type(reviewer_id) is not str
        or _REVIEWER_ID_RE.fullmatch(reviewer_id) is None
    ):
        raise HumanReviewError("reviewer_id is invalid")

    reviewer_version = value["reviewer_version"]
    if type(reviewer_version) is not int or reviewer_version < 1:
        raise HumanReviewError("reviewer_version must be a positive integer")

    decision = value["decision"]
    if type(decision) is not str or decision not in HUMAN_REVIEW_DECISIONS:
        raise HumanReviewError("decision is invalid")

    return {
        "artifact": artifact,
        "v": version,
        "response_digest": response_digest,
        "reviewer_id": reviewer_id,
        "reviewer_version": reviewer_version,
        "decision": decision,
    }
