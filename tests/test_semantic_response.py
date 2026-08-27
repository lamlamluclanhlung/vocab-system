"""Deterministic tests for the closed D59 T11 semantic-response import."""

from __future__ import annotations

import ast
import copy
import inspect
import json
from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType

import pytest

import vocab.semantic_response as semantic_response_module
from vocab.artifact_json import canonical_json_bytes
from vocab.contracts import (
    ASSESSMENT_ABSTAIN_REASON_CODES,
    ASSESSMENT_FAILURE_CODES_BY_CHANNEL,
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_PASS,
)
from vocab.semantic_request import (
    SemanticRequestError,
    build_semantic_request,
    semantic_request_digest,
)
from vocab.semantic_response import (
    ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES,
    SEMANTIC_RESPONSE_ARTIFACT,
    SEMANTIC_RESPONSE_OUTCOMES,
    SEMANTIC_RESPONSE_VERSION,
    ImportedSemanticProposal,
    SemanticResponseError,
    canonical_semantic_proposal_bytes,
    import_semantic_response,
)
from vocab.validators import validate_t11_assessment_result


GOLDEN_REQUEST_DIGEST = (
    "7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e"
)
GOLDEN_RESPONSE_BYTES = (
    b'{"artifact":"vocab.t11.semantic-response","failure_code":"","outcome":"PASS",'
    b'"reason_code":"","request_digest":"7dc54e64201a96eff73a8c9f75b0841bc38128dd8e6f214513da432fa6730e5e",'
    b'"semantic_rationale":"The learner correctly paraphrases the target sense as a slight, hard-to-notice difference.","v":1}'
)
GOLDEN_RESPONSE_DIGEST = (
    "c46c6db07c702f5d7b4ea45f778883307611d1b5548c3b05dc3f6f5dec4453ab"
)

TASK_CONTENT_BY_CHANNEL = {
    "R": {
        "passage": "Sự khác biệt giữa hai phương án rất subtle.",
        "question": "What does subtle mean in this passage?",
        "learner_response": (
            "It means the difference is slight and not immediately obvious."
        ),
    },
    "L": {
        "spoken_script": "The distinction between the proposals was subtle.",
        "question": "How did the speaker describe the distinction?",
        "learner_response": "The distinction was slight and hard to notice.",
    },
    "W": {
        "production_prompt": "Compare two similar research results.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "learner_response": "The studies showed a subtle difference in timing.",
    },
    "S": {
        "production_prompt": "Describe a small difference between two plans.",
        "semantic_constraints": "Use subtle for a small, hard-to-notice difference.",
        "approved_transcript": "There is a subtle difference in their priorities.",
    },
}

PROPOSAL_KEYS = {
    "artifact",
    "v",
    "request_digest",
    "outcome",
    "failure_code",
    "reason_code",
    "semantic_rationale",
}
FACT_KEYS = {
    "protocol_id",
    "protocol_version",
    "rubric_id",
    "rubric_version",
    "prompt_id",
    "prompt_version",
    "assessor_id",
    "assessor_version",
    "request_digest",
    "response_digest",
}


def make_request(channel: str = "R") -> dict[str, object]:
    return build_semantic_request(
        unit_key="subtle::small-difference",
        lemma="subtle",
        unit_type="word",
        definition_en="not immediately obvious; tinh tế",
        channel=channel,
        task_content=TASK_CONTENT_BY_CHANNEL[channel],
    )


def make_proposal(
    request: object,
    **overrides: object,
) -> dict[str, object]:
    proposal: dict[str, object] = {
        "artifact": "vocab.t11.semantic-response",
        "v": 1,
        "request_digest": semantic_request_digest(request),
        "outcome": "PASS",
        "failure_code": "",
        "reason_code": "",
        "semantic_rationale": (
            "The learner correctly paraphrases the target sense as a slight, "
            "hard-to-notice difference."
        ),
    }
    proposal.update(overrides)
    return proposal


def transport_bytes(value: object, **kwargs: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, **kwargs).encode("utf-8")


def import_proposal(
    proposal: object,
    *,
    request: object | None = None,
    assessor_id: object = "GPT-5.6",
    assessor_version: object = ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
) -> ImportedSemanticProposal:
    bound_request = make_request() if request is None else request
    return import_semantic_response(
        transport_bytes(proposal),
        request=bound_request,
        assessor_id=assessor_id,  # type: ignore[arg-type]
        assessor_version=assessor_version,  # type: ignore[arg-type]
    )


def test_frozen_response_constants_are_exact() -> None:
    assert (
        SEMANTIC_RESPONSE_ARTIFACT,
        SEMANTIC_RESPONSE_VERSION,
        SEMANTIC_RESPONSE_OUTCOMES,
        SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES,
        ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    ) == (
        "vocab.t11.semantic-response",
        1,
        ("PASS", "FAIL", "ABSTAIN"),
        (
            "off_topic",
            "insufficient_lexical_evidence",
            "response_unintelligible",
            "semantic_uncertainty",
        ),
        "version-unavailable-from-ui",
    )


def test_golden_response_bytes_length_and_digest_are_independently_frozen() -> None:
    request = make_request()
    imported = import_semantic_response(
        GOLDEN_RESPONSE_BYTES,
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )

    assert semantic_request_digest(request) == GOLDEN_REQUEST_DIGEST
    assert len(GOLDEN_RESPONSE_BYTES) == 298
    assert canonical_semantic_proposal_bytes(imported) == GOLDEN_RESPONSE_BYTES
    assert imported.response_digest == GOLDEN_RESPONSE_DIGEST


def test_import_returns_exact_closed_proposal_result_and_facts() -> None:
    request = make_request()
    imported = import_proposal(make_proposal(request), request=request)

    assert set(imported.proposal) == PROPOSAL_KEYS
    assert "response_digest" not in imported.proposal
    assert "assessor_id" not in imported.proposal
    assert "assessor_version" not in imported.proposal
    assert imported.request_digest == GOLDEN_REQUEST_DIGEST
    assert imported.assessment_result.unit_key == "subtle::small-difference"
    assert imported.assessment_result.channel == "R"
    assert imported.assessment_result.outcome == ASSESSMENT_OUTCOME_PASS
    assert imported.assessment_result.failure_code == ""
    assert imported.assessment_result.reason_code == ""
    assert validate_t11_assessment_result(imported.assessment_result) == ()
    assert set(imported.semantic_judge_facts) == FACT_KEYS


def test_runtime_import_record_is_frozen_slotted_and_deeply_immutable() -> None:
    request = make_request()
    imported = import_proposal(make_proposal(request), request=request)

    assert tuple(field.name for field in fields(ImportedSemanticProposal)) == (
        "proposal",
        "assessment_result",
        "assessor_id",
        "assessor_version",
        "request_digest",
        "response_digest",
        "semantic_judge_facts",
    )
    assert not hasattr(imported, "__dict__")
    with pytest.raises(FrozenInstanceError):
        imported.assessor_id = "another"  # type: ignore[misc]
    with pytest.raises(TypeError):
        imported.proposal["outcome"] = "FAIL"
    with pytest.raises(TypeError):
        imported.semantic_judge_facts["assessor_id"] = "another"


@pytest.mark.parametrize("channel", ["R", "L", "W", "S"])
def test_pass_derives_unit_and_each_request_channel(channel: str) -> None:
    request = make_request(channel)
    imported = import_proposal(make_proposal(request), request=request)

    result = imported.assessment_result
    assert (
        result.unit_key,
        result.channel,
        result.outcome,
        result.failure_code,
        result.reason_code,
    ) == ("subtle::small-difference", channel, "PASS", "", "")
    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize(
    ("channel", "failure_code"),
    [
        (channel, failure_code)
        for channel, failure_codes in ASSESSMENT_FAILURE_CODES_BY_CHANNEL.items()
        for failure_code in failure_codes
    ],
)
def test_each_channel_failure_code_derives_a_valid_generic_result(
    channel: str,
    failure_code: str,
) -> None:
    request = make_request(channel)
    proposal = make_proposal(
        request,
        outcome=ASSESSMENT_OUTCOME_FAIL,
        failure_code=failure_code,
    )

    result = import_proposal(proposal, request=request).assessment_result

    assert (
        result.unit_key,
        result.channel,
        result.outcome,
        result.failure_code,
        result.reason_code,
    ) == (
        "subtle::small-difference",
        channel,
        ASSESSMENT_OUTCOME_FAIL,
        failure_code,
        "",
    )
    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize("reason_code", SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES)
def test_each_semantic_abstain_reason_derives_a_valid_generic_result(
    reason_code: str,
) -> None:
    request = make_request()
    proposal = make_proposal(
        request,
        outcome=ASSESSMENT_OUTCOME_ABSTAIN,
        reason_code=reason_code,
    )

    result = import_proposal(proposal, request=request).assessment_result

    assert result.outcome == ASSESSMENT_OUTCOME_ABSTAIN
    assert result.failure_code == ""
    assert result.reason_code == reason_code
    assert validate_t11_assessment_result(result) == ()


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{",
        b'{"artifact":"x","artifact":"y"}',
        b'{"outer":{"key":1,"key":2}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e999}',
        b'{"value":-1e999}',
    ],
)
def test_strict_transport_failures_are_semantic_response_errors(raw: bytes) -> None:
    with pytest.raises(SemanticResponseError):
        import_semantic_response(
            raw,
            request=make_request(),
            assessor_id="GPT-5.6",
            assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        )


def test_non_bytes_raw_remains_type_error() -> None:
    with pytest.raises(TypeError):
        import_semantic_response(
            "{}",  # type: ignore[arg-type]
            request=make_request(),
            assessor_id="GPT-5.6",
            assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        )


@pytest.mark.parametrize("raw", [b"[]", b"1", b'"proposal"', b"null", b"true"])
def test_top_level_array_and_scalars_fail_closed(raw: bytes) -> None:
    with pytest.raises(SemanticResponseError):
        import_semantic_response(
            raw,
            request=make_request(),
            assessor_id="GPT-5.6",
            assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        )


def test_unknown_proposal_key_fails_closed() -> None:
    request = make_request()
    proposal = make_proposal(request)
    proposal["model_id"] = "forbidden"

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize("missing", sorted(PROPOSAL_KEYS))
def test_each_missing_proposal_key_fails_closed(missing: str) -> None:
    request = make_request()
    proposal = make_proposal(request)
    del proposal[missing]

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize("field", sorted(PROPOSAL_KEYS))
def test_null_in_each_required_proposal_field_fails_closed(field: str) -> None:
    request = make_request()
    proposal = make_proposal(request, **{field: None})

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("artifact", "vocab.t11.semantic-request"),
        ("v", True),
        ("v", 1.0),
        ("v", 2),
    ],
)
def test_discriminator_and_actual_integer_version_are_exact(
    field: str,
    bad_value: object,
) -> None:
    request = make_request()
    proposal = make_proposal(request, **{field: bad_value})

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize(
    "digest",
    [
        1,
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "sha256:" + ("a" * 64),
        "g" * 64,
    ],
)
def test_request_digest_requires_exact_bare_lowercase_sha256(digest: object) -> None:
    request = make_request()
    proposal = make_proposal(request, request_digest=digest)

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


def test_valid_looking_digest_for_another_request_fails_binding() -> None:
    request = make_request()
    other_request = make_request("L")
    proposal = make_proposal(
        request,
        request_digest=semantic_request_digest(other_request),
    )

    with pytest.raises(SemanticResponseError, match="bind"):
        import_proposal(proposal, request=request)


def test_read_only_top_level_mapping_uses_t11_request_authorities() -> None:
    base_request = make_request()
    proxy_request = MappingProxyType(base_request)
    proposal = make_proposal(base_request)
    plain = import_proposal(proposal, request=base_request)

    imported = import_semantic_response(
        transport_bytes(proposal),
        request=proxy_request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )

    assert imported.request_digest == semantic_request_digest(base_request)
    assert imported.request_digest == GOLDEN_REQUEST_DIGEST
    assert imported.response_digest == GOLDEN_RESPONSE_DIGEST
    assert imported.assessment_result == plain.assessment_result
    assert imported.semantic_judge_facts == plain.semantic_judge_facts
    assert imported.proposal == plain.proposal


def test_nested_read_only_mappings_have_identical_request_and_response_identity() -> None:
    base_request = make_request()
    nested_request = {
        key: MappingProxyType(value) if isinstance(value, dict) else value
        for key, value in base_request.items()
    }
    proxy_request = MappingProxyType(nested_request)
    proposal = make_proposal(base_request)
    plain = import_proposal(proposal, request=base_request)

    imported = import_semantic_response(
        transport_bytes(proposal),
        request=proxy_request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )

    assert semantic_request_digest(proxy_request) == semantic_request_digest(
        base_request
    )
    assert imported.request_digest == plain.request_digest
    assert imported.response_digest == plain.response_digest
    assert imported.response_digest == GOLDEN_RESPONSE_DIGEST
    assert imported.assessment_result == plain.assessment_result
    assert imported.semantic_judge_facts == plain.semantic_judge_facts
    assert imported.proposal == plain.proposal


def test_invalid_supplied_request_propagates_semantic_request_error() -> None:
    request = make_request()
    request["v"] = True
    proposal = make_proposal(make_request())

    with pytest.raises(SemanticRequestError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize("outcome", [1, True, "OMITTED", "UNKNOWN", "pass"])
def test_outcome_must_be_exact_semantic_subset(outcome: object) -> None:
    request = make_request()
    proposal = make_proposal(request, outcome=outcome)

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize(
    "overrides",
    [
        {"failure_code": "wrong_meaning"},
        {"reason_code": "off_topic"},
        {"failure_code": "wrong_meaning", "reason_code": "off_topic"},
    ],
)
def test_pass_requires_both_code_strings_empty(overrides: dict[str, object]) -> None:
    request = make_request()

    with pytest.raises(SemanticResponseError):
        import_proposal(make_proposal(request, **overrides), request=request)


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "FAIL", "failure_code": ""},
        {"outcome": "FAIL", "failure_code": "wrong_meaning", "reason_code": "off_topic"},
        {"outcome": "FAIL", "failure_code": "wrong_interpretation"},
        {"outcome": "FAIL", "failure_code": "semantic_misuse"},
    ],
)
def test_fail_requires_request_channel_code_and_empty_reason(
    overrides: dict[str, object],
) -> None:
    request = make_request("R")

    with pytest.raises(SemanticResponseError):
        import_proposal(make_proposal(request, **overrides), request=request)


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "ABSTAIN", "failure_code": "wrong_meaning", "reason_code": "off_topic"},
        {"outcome": "ABSTAIN", "reason_code": ""},
    ],
)
def test_abstain_requires_empty_failure_and_owned_reason(
    overrides: dict[str, object],
) -> None:
    request = make_request()

    with pytest.raises(SemanticResponseError):
        import_proposal(make_proposal(request, **overrides), request=request)


NON_SEMANTIC_ABSTAIN_REASONS = tuple(
    reason
    for reason in ASSESSMENT_ABSTAIN_REASON_CODES
    if reason not in SEMANTIC_RESPONSE_ABSTAIN_REASON_CODES
)


@pytest.mark.parametrize("reason_code", NON_SEMANTIC_ABSTAIN_REASONS)
def test_every_non_semantic_generic_abstain_reason_is_rejected(
    reason_code: str,
) -> None:
    request = make_request()
    proposal = make_proposal(
        request,
        outcome="ABSTAIN",
        reason_code=reason_code,
    )

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


@pytest.mark.parametrize("field", ["failure_code", "reason_code"])
@pytest.mark.parametrize("bad_value", [1, True, [], {}])
def test_code_fields_are_exact_strings(field: str, bad_value: object) -> None:
    request = make_request()

    with pytest.raises(SemanticResponseError):
        import_proposal(
            make_proposal(request, **{field: bad_value}),
            request=request,
        )


@pytest.mark.parametrize("rationale", [1, True, None, "", " \t\n "])
def test_semantic_rationale_type_content_and_utf8_totality(rationale: object) -> None:
    request = make_request()
    proposal = make_proposal(request, semantic_rationale=rationale)

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request)


def test_semantic_rationale_rejects_json_escaped_surrogate() -> None:
    request = make_request()
    proposal = make_proposal(request, semantic_rationale="\ud800")
    raw = json.dumps(proposal, ensure_ascii=True).encode("utf-8")

    with pytest.raises(SemanticResponseError):
        import_semantic_response(
            raw,
            request=request,
            assessor_id="GPT-5.6",
            assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        )


def test_semantic_rationale_unicode_and_whitespace_are_preserved_verbatim() -> None:
    request = make_request()
    rationale = "  Bằng chứng từ người học là tinh tế. \u200b  "
    proposal = make_proposal(request, semantic_rationale=rationale)

    imported = import_proposal(proposal, request=request)

    assert imported.proposal["semantic_rationale"] == rationale
    assert rationale.encode("utf-8") in canonical_semantic_proposal_bytes(imported)


@pytest.mark.parametrize(
    "bad_value",
    [
        1,
        True,
        None,
        "",
        "   ",
        " leading",
        "trailing ",
        "x" * 129,
        "\t",
        "\n",
        "a\tb",
        "a\nb",
        "a\x00b",
        "a\x1fb",
    ],
)
@pytest.mark.parametrize("field", ["assessor_id", "assessor_version"])
def test_assessor_metadata_hygiene_fails_closed(
    field: str,
    bad_value: object,
) -> None:
    request = make_request()
    proposal = make_proposal(request)
    kwargs = {
        "assessor_id": "GPT-5.6",
        "assessor_version": ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        field: bad_value,
    }

    with pytest.raises(SemanticResponseError):
        import_proposal(proposal, request=request, **kwargs)


def test_visible_unicode_assessor_metadata_is_preserved_verbatim() -> None:
    request = make_request()
    proposal = make_proposal(request)
    imported = import_proposal(
        proposal,
        request=request,
        assessor_id="GPT-5.6 — 学習",
        assessor_version="build-2026.08.24 β",
    )

    assert imported.assessor_id == "GPT-5.6 — 学習"
    assert imported.assessor_version == "build-2026.08.24 β"
    assert imported.semantic_judge_facts["assessor_id"] == "GPT-5.6 — 学習"
    assert imported.semantic_judge_facts["assessor_version"] == "build-2026.08.24 β"


def test_unavailable_version_sentinel_is_accepted_exactly() -> None:
    request = make_request()
    imported = import_proposal(make_proposal(request), request=request)

    assert imported.assessor_version == "version-unavailable-from-ui"


def test_transport_order_and_insignificant_whitespace_do_not_change_response() -> None:
    request = make_request()
    proposal = make_proposal(request)
    reordered = {key: proposal[key] for key in reversed(tuple(proposal))}

    compact = import_semantic_response(
        transport_bytes(reordered, separators=(",", ":")),
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )
    pretty = import_semantic_response(
        transport_bytes(reordered, indent=4),
        request=request,
        assessor_id="GPT-5.6",
        assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
    )

    assert canonical_semantic_proposal_bytes(compact) == canonical_semantic_proposal_bytes(pretty)
    assert compact.response_digest == pretty.response_digest


def test_one_character_rationale_change_changes_response_digest() -> None:
    request = make_request()
    baseline = import_proposal(make_proposal(request), request=request)
    changed = import_proposal(
        make_proposal(
            request,
            semantic_rationale=(
                "The learner correctly paraphrases the target sense as a slight, "
                "hard-to-notice difference!"
            ),
        ),
        request=request,
    )

    assert baseline.response_digest != changed.response_digest


def test_valid_outcome_change_changes_response_digest() -> None:
    request = make_request()
    passed = import_proposal(make_proposal(request), request=request)
    abstained = import_proposal(
        make_proposal(
            request,
            outcome="ABSTAIN",
            reason_code="semantic_uncertainty",
        ),
        request=request,
    )

    assert passed.response_digest != abstained.response_digest


def test_bound_request_digest_change_changes_valid_proposal_identity() -> None:
    first_request = make_request()
    second_request = make_request("L")
    first = import_proposal(make_proposal(first_request), request=first_request)
    second = import_proposal(make_proposal(second_request), request=second_request)

    assert first.request_digest != second.request_digest
    assert first.response_digest != second.response_digest


def test_old_proposal_fails_after_learner_evidence_mutates() -> None:
    request = make_request()
    proposal = make_proposal(request)
    changed = copy.deepcopy(request)
    task = changed["task"]
    assert isinstance(task, dict)
    task["learner_response"] += " Changed evidence."

    assert semantic_request_digest(request) != semantic_request_digest(changed)
    with pytest.raises(SemanticResponseError, match="bind"):
        import_proposal(proposal, request=changed)


def test_same_proposal_different_assessor_keeps_digest_but_changes_facts() -> None:
    request = make_request()
    proposal = make_proposal(request)
    first = import_proposal(
        proposal,
        request=request,
        assessor_id="GPT-5.6",
        assessor_version="build-a",
    )
    second = import_proposal(
        proposal,
        request=request,
        assessor_id="GPT-5.6 Pro",
        assessor_version="build-b",
    )

    assert first.response_digest == second.response_digest
    assert canonical_semantic_proposal_bytes(first) == canonical_semantic_proposal_bytes(second)
    assert first.assessor_id != second.assessor_id
    assert first.assessor_version != second.assessor_version
    assert first.semantic_judge_facts != second.semantic_judge_facts
    assert first.semantic_judge_facts["response_digest"] == second.semantic_judge_facts["response_digest"]


def test_semantic_judge_facts_have_exact_sources_and_no_wrapper() -> None:
    request = make_request()
    imported = import_proposal(
        make_proposal(request),
        request=request,
        assessor_id="GPT-5.6",
        assessor_version="build-42",
    )

    assert imported.semantic_judge_facts == {
        "protocol_id": request["protocol"]["id"],
        "protocol_version": request["protocol"]["version"],
        "rubric_id": request["rubric"]["id"],
        "rubric_version": request["rubric"]["version"],
        "prompt_id": request["prompt"]["id"],
        "prompt_version": request["prompt"]["version"],
        "assessor_id": "GPT-5.6",
        "assessor_version": "build-42",
        "request_digest": semantic_request_digest(request),
        "response_digest": imported.response_digest,
    }
    assert "semantic_judge" not in imported.semantic_judge_facts


PROHIBITED_PROPOSAL_AND_FACT_KEYS = {
    "model_id",
    "model_version",
    "authority_kind",
    "reviewer_id",
    "decision",
    "session_id",
    "item_ordinal",
    "attempt_id",
    "assessment_id",
    "stimulus_ref",
    "presented_stimulus_ref",
    "stimulus_artifact_ref",
    "response_artifact_ref",
    "response_audio_ref",
    "novel",
    "producer",
    "producer_version",
    "reserved_at",
    "ts",
    "day",
}


def test_runtime_output_has_no_t12_eventlog_or_review_owned_fields() -> None:
    request = make_request()
    imported = import_proposal(make_proposal(request), request=request)

    assert set(imported.proposal).isdisjoint(PROHIBITED_PROPOSAL_AND_FACT_KEYS)
    assert set(imported.semantic_judge_facts).isdisjoint(
        PROHIBITED_PROPOSAL_AND_FACT_KEYS
    )


def test_import_api_cannot_omit_explicit_request_or_assessor_metadata() -> None:
    request = make_request()
    raw = transport_bytes(make_proposal(request))

    with pytest.raises(TypeError):
        import_semantic_response(  # type: ignore[call-arg]
            raw,
            assessor_id="GPT-5.6",
            assessor_version=ASSESSOR_VERSION_UNAVAILABLE_FROM_UI,
        )
    with pytest.raises(TypeError):
        import_semantic_response(raw, request=request)  # type: ignore[call-arg]


def test_canonical_bytes_helper_requires_accepted_runtime_import() -> None:
    with pytest.raises(TypeError):
        canonical_semantic_proposal_bytes({})  # type: ignore[arg-type]


def test_semantic_response_module_has_no_io_eventlog_anki_or_t12_imports() -> None:
    tree = ast.parse(inspect.getsource(semantic_response_module))
    prohibited = {
        "os",
        "pathlib",
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
