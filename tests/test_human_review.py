"""Deterministic tests for the closed D59 T11 human-review artifact."""

from __future__ import annotations

import ast
import copy
import inspect
import json
from dataclasses import FrozenInstanceError, fields, replace
from types import MappingProxyType

import pytest

import vocab.human_review as human_review_module
from vocab.contracts import HUMAN_REVIEW_DECISIONS, SLUG_PATTERN
from vocab.human_review import (
    HUMAN_REVIEW_ARTIFACT,
    HUMAN_REVIEW_VERSION,
    HumanReviewError,
    ImportedHumanReview,
    build_human_review,
    import_human_review,
    serialize_human_review,
)
from vocab.semantic_request import build_semantic_request, semantic_request_digest
from vocab.semantic_response import (
    ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    ImportedSemanticProposal,
    import_semantic_response,
)


GOLDEN_REQUEST_DIGEST = (
    "7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e"
)
GOLDEN_RESPONSE_DIGEST = (
    "c46c6db07c702f5d7b4ea45f778883307611d1b5548c3b05dc3f6f5dec4453ab"
)
GOLDEN_RESPONSE_BYTES = (
    b'{"artifact":"vocab.t11.semantic-response","failure_code":"","outcome":"PASS",'
    b'"reason_code":"","request_digest":"7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e",'
    b'"semantic_rationale":"The learner correctly paraphrases the target sense as a slight, hard-to-notice difference.","v":1}'
)
GOLDEN_REVIEW_BYTES = (
    b'{"artifact":"vocab.t11.human-review","decision":"APPROVE",'
    b'"response_digest":"c46c6db07c702f5d7b4ea45f778883307611d1b5548c3b05dc3f6f5dec4453ab",'
    b'"reviewer_id":"reviewer-a","reviewer_version":1,"v":1}'
)

REVIEW_FIELDS = {
    "artifact",
    "v",
    "response_digest",
    "reviewer_id",
    "reviewer_version",
    "decision",
}


def make_request() -> dict[str, object]:
    return build_semantic_request(
        unit_key="subtle::small-difference",
        lemma="subtle",
        unit_type="word",
        definition_en="not immediately obvious; tinh tế",
        channel="R",
        task_content={
            "passage": "Sự khác biệt giữa hai phương án rất subtle.",
            "question": "What does subtle mean in this passage?",
            "learner_response": (
                "It means the difference is slight and not immediately obvious."
            ),
        },
    )


def make_imported_proposal(
    *,
    semantic_rationale: str | None = None,
) -> ImportedSemanticProposal:
    request = make_request()
    if semantic_rationale is None:
        raw = GOLDEN_RESPONSE_BYTES
    else:
        proposal = json.loads(GOLDEN_RESPONSE_BYTES)
        proposal["semantic_rationale"] = semantic_rationale
        raw = transport_bytes(proposal)
    return import_semantic_response(
        raw,
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )


def make_review(
    imported_proposal: ImportedSemanticProposal,
    **overrides: object,
) -> dict[str, object]:
    review: dict[str, object] = {
        "artifact": "vocab.t11.human-review",
        "v": 1,
        "response_digest": imported_proposal.response_digest,
        "reviewer_id": "reviewer-a",
        "reviewer_version": 1,
        "decision": "APPROVE",
    }
    review.update(overrides)
    return review


def transport_bytes(value: object, **kwargs: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, **kwargs).encode("utf-8")


def import_review(
    review: object,
    imported_proposal: ImportedSemanticProposal,
) -> ImportedHumanReview:
    return import_human_review(
        transport_bytes(review),
        imported_proposal=imported_proposal,
    )


def test_frozen_human_review_constants_reuse_existing_authorities() -> None:
    assert HUMAN_REVIEW_ARTIFACT == "vocab.t11.human-review"
    assert HUMAN_REVIEW_VERSION == 1
    assert HUMAN_REVIEW_DECISIONS == ("APPROVE", "REJECT")
    assert SLUG_PATTERN == r"[a-z0-9]+(?:-[a-z0-9]+)*"


def test_golden_review_vector_has_exact_independent_bytes_and_length() -> None:
    imported_proposal = make_imported_proposal()
    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision="APPROVE",
    )

    assert semantic_request_digest(make_request()) == GOLDEN_REQUEST_DIGEST
    assert imported_proposal.response_digest == GOLDEN_RESPONSE_DIGEST
    assert len(GOLDEN_REVIEW_BYTES) == 197
    assert review == json.loads(GOLDEN_REVIEW_BYTES)
    assert serialize_human_review(
        review,
        imported_proposal=imported_proposal,
    ) == GOLDEN_REVIEW_BYTES


@pytest.mark.parametrize("decision", HUMAN_REVIEW_DECISIONS)
def test_build_and_import_accept_exact_approve_and_reject(decision: str) -> None:
    imported_proposal = make_imported_proposal()
    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision=decision,
    )

    imported = import_review(review, imported_proposal)

    assert set(review) == REVIEW_FIELDS
    assert imported.review == review
    assert imported.response_digest == GOLDEN_RESPONSE_DIGEST
    assert imported.reviewer_id == "reviewer-a"
    assert imported.reviewer_version == 1
    assert imported.decision == decision


def test_builder_derives_response_digest_without_caller_digest_parameter() -> None:
    imported_proposal = make_imported_proposal()

    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision="APPROVE",
    )

    assert review["response_digest"] == imported_proposal.response_digest
    with pytest.raises(TypeError):
        build_human_review(  # type: ignore[call-arg]
            imported_proposal=imported_proposal,
            response_digest="0" * 64,
            reviewer_id="reviewer-a",
            reviewer_version=1,
            decision="APPROVE",
        )


def test_runtime_review_is_frozen_slotted_and_mapping_is_immutable() -> None:
    imported_proposal = make_imported_proposal()
    imported = import_human_review(
        GOLDEN_REVIEW_BYTES,
        imported_proposal=imported_proposal,
    )

    assert tuple(field.name for field in fields(ImportedHumanReview)) == (
        "review",
        "response_digest",
        "reviewer_id",
        "reviewer_version",
        "decision",
    )
    assert not hasattr(imported, "__dict__")
    with pytest.raises(FrozenInstanceError):
        imported.decision = "REJECT"  # type: ignore[misc]
    with pytest.raises(TypeError):
        imported.review["decision"] = "REJECT"


def test_imported_review_does_not_alias_caller_owned_mapping() -> None:
    imported_proposal = make_imported_proposal()
    source = make_review(imported_proposal)
    imported = import_review(source, imported_proposal)

    source["decision"] = "REJECT"

    assert imported.review["decision"] == "APPROVE"


@pytest.mark.parametrize(
    ("reviewer_id", "reviewer_version"),
    [
        ("reviewer-a", 1),
        ("reviewer-1", 1),
        ("reviewer-alpha-2", 2),
        ("r", 7),
    ],
)
def test_valid_slug_reviewers_and_positive_versions_are_preserved(
    reviewer_id: str,
    reviewer_version: int,
) -> None:
    imported_proposal = make_imported_proposal()
    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id=reviewer_id,
        reviewer_version=reviewer_version,
        decision="APPROVE",
    )

    assert review["reviewer_id"] == reviewer_id
    assert review["reviewer_version"] == reviewer_version


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{",
        b'{"artifact":"x","artifact":"y"}',
        b'{"extra":{"key":1,"key":2}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e999}',
        b'{"value":-1e999}',
    ],
)
def test_strict_transport_failures_use_focused_review_error(raw: bytes) -> None:
    with pytest.raises(HumanReviewError):
        import_human_review(raw, imported_proposal=make_imported_proposal())


def test_non_bytes_raw_remains_type_error() -> None:
    with pytest.raises(TypeError):
        import_human_review(  # type: ignore[arg-type]
            "{}",
            imported_proposal=make_imported_proposal(),
        )


@pytest.mark.parametrize("raw", [b"[]", b"1", b'"review"', b"null", b"true"])
def test_top_level_array_and_scalars_fail_closed(raw: bytes) -> None:
    with pytest.raises(HumanReviewError):
        import_human_review(raw, imported_proposal=make_imported_proposal())


def test_unknown_review_key_fails_closed() -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal)
    review["extra"] = "forbidden"

    with pytest.raises(HumanReviewError):
        import_review(review, imported_proposal)


@pytest.mark.parametrize("missing", sorted(REVIEW_FIELDS))
def test_each_missing_review_key_fails_closed(missing: str) -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal)
    del review[missing]

    with pytest.raises(HumanReviewError):
        import_review(review, imported_proposal)


@pytest.mark.parametrize("field", sorted(REVIEW_FIELDS))
def test_null_in_each_required_review_field_fails_closed(field: str) -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal, **{field: None})

    with pytest.raises(HumanReviewError):
        import_review(review, imported_proposal)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("artifact", "vocab.t11.semantic-response"),
        ("v", True),
        ("v", 1.0),
        ("v", 0),
        ("v", 2),
        ("v", "1"),
    ],
)
def test_artifact_and_actual_integer_version_are_exact(
    field: str,
    bad_value: object,
) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(HumanReviewError):
        import_review(
            make_review(imported_proposal, **{field: bad_value}),
            imported_proposal,
        )


@pytest.mark.parametrize(
    "digest",
    [
        1,
        True,
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "sha256:" + ("a" * 64),
        " " + ("a" * 64),
        ("a" * 64) + " ",
    ],
)
def test_response_digest_requires_exact_bare_lowercase_sha256(digest: object) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(HumanReviewError):
        import_review(
            make_review(imported_proposal, response_digest=digest),
            imported_proposal,
        )


@pytest.mark.parametrize("decision", HUMAN_REVIEW_DECISIONS)
def test_valid_digest_for_another_proposal_fails_for_every_decision(
    decision: str,
) -> None:
    first = make_imported_proposal()
    second = make_imported_proposal(
        semantic_rationale=(
            "The learner correctly paraphrases the target sense with changed wording."
        )
    )
    review = build_human_review(
        imported_proposal=first,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision=decision,
    )

    assert first.response_digest != second.response_digest
    with pytest.raises(HumanReviewError, match="bind"):
        import_review(review, second)


def test_importer_independently_rejects_inconsistent_runtime_response_digest() -> None:
    imported_proposal = make_imported_proposal()
    inconsistent = replace(imported_proposal, response_digest="0" * 64)
    review = make_review(imported_proposal)

    with pytest.raises(HumanReviewError, match="proposal content"):
        import_review(review, inconsistent)
    with pytest.raises(HumanReviewError, match="proposal content"):
        build_human_review(
            imported_proposal=inconsistent,
            reviewer_id="reviewer-a",
            reviewer_version=1,
            decision="APPROVE",
        )


@pytest.mark.parametrize(
    "reviewer_id",
    [
        1,
        True,
        "",
        "Reviewer-A",
        "reviewer_a",
        " reviewer-a",
        "reviewer-a ",
        "reviewer--a",
        "-reviewer-a",
        "reviewer-a-",
        "reviewer.a",
        "reviewer/a",
        "réviseur-a",
    ],
)
def test_reviewer_id_reuses_exact_slug_pattern_without_repair(
    reviewer_id: object,
) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(HumanReviewError):
        import_review(
            make_review(imported_proposal, reviewer_id=reviewer_id),
            imported_proposal,
        )


@pytest.mark.parametrize("reviewer_version", [True, 1.0, 0, -1, "1", None])
def test_reviewer_version_requires_actual_positive_integer(
    reviewer_version: object,
) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(HumanReviewError):
        import_review(
            make_review(imported_proposal, reviewer_version=reviewer_version),
            imported_proposal,
        )


@pytest.mark.parametrize(
    "decision",
    [1, True, "", "approve", "reject", "PASS", "FAIL", "ABSTAIN", "ACCEPT", "DENY", "UNKNOWN"],
)
def test_decision_reuses_exact_existing_inventory(decision: object) -> None:
    imported_proposal = make_imported_proposal()

    with pytest.raises(HumanReviewError):
        import_review(
            make_review(imported_proposal, decision=decision),
            imported_proposal,
        )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "outcome",
        "failure_code",
        "reason_code",
        "semantic_rationale",
        "replacement_proposal",
        "proposal",
        "unit_key",
        "channel",
        "request_digest",
        "assessor_id",
        "session_id",
        "attempt_id",
        "payload",
        "model_id",
    ],
)
def test_reviewer_cannot_add_semantic_t12_or_eventlog_fields(
    forbidden_field: str,
) -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal, decision="REJECT")
    review[forbidden_field] = "forbidden"

    with pytest.raises(HumanReviewError):
        import_review(review, imported_proposal)


def test_transport_order_and_whitespace_canonicalize_identically() -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal)
    reordered = {key: review[key] for key in reversed(tuple(review))}

    compact = import_human_review(
        transport_bytes(reordered, separators=(",", ":")),
        imported_proposal=imported_proposal,
    )
    pretty = import_human_review(
        transport_bytes(reordered, indent=4),
        imported_proposal=imported_proposal,
    )

    assert compact == pretty
    assert serialize_human_review(
        compact.review,
        imported_proposal=imported_proposal,
    ) == GOLDEN_REVIEW_BYTES
    assert serialize_human_review(
        pretty.review,
        imported_proposal=imported_proposal,
    ) == GOLDEN_REVIEW_BYTES


def test_serializer_accepts_read_only_mapping_without_an_alias() -> None:
    imported_proposal = make_imported_proposal()
    review = make_review(imported_proposal)
    proxy = MappingProxyType(review)

    assert serialize_human_review(
        proxy,
        imported_proposal=imported_proposal,
    ) == GOLDEN_REVIEW_BYTES


def test_import_api_requires_exact_proposal_under_review() -> None:
    with pytest.raises(TypeError):
        import_human_review(GOLDEN_REVIEW_BYTES)  # type: ignore[call-arg]


def test_t113c_does_not_materialize_or_invent_review_identity() -> None:
    imported = import_human_review(
        GOLDEN_REVIEW_BYTES,
        imported_proposal=make_imported_proposal(),
    )

    assert set(imported.review) == REVIEW_FIELDS
    assert not hasattr(imported, "assessment_result")
    assert not hasattr(imported, "outcome")
    assert not hasattr(imported, "failure_code")
    assert not hasattr(imported, "reason_code")
    assert not hasattr(imported, "review_digest")
    assert not hasattr(imported, "review_id")
    assert "review_digest" not in imported.review
    assert "review_id" not in imported.review


def test_human_review_module_has_no_materialization_io_or_history_imports() -> None:
    tree = ast.parse(inspect.getsource(human_review_module))
    prohibited = {
        "models",
        "validators",
        "events",
        "anki",
        "reconcile",
        "session",
        "tts",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[-1])

    assert imported.isdisjoint(prohibited)


def test_helpers_do_not_mutate_imported_proposal() -> None:
    imported_proposal = make_imported_proposal()
    before = copy.deepcopy(dict(imported_proposal.proposal))

    review = build_human_review(
        imported_proposal=imported_proposal,
        reviewer_id="reviewer-a",
        reviewer_version=1,
        decision="APPROVE",
    )
    import_review(review, imported_proposal)

    assert dict(imported_proposal.proposal) == before
