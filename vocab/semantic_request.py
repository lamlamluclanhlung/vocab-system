"""Pure T11 semantic-request construction, import, and identity helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from .contracts import (
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    CHANNELS,
    UNIT_KEY_PATTERN,
    UNIT_TYPE_VALUES,
)


SEMANTIC_REQUEST_ARTIFACT = "vocab.t11.semantic-request"
SEMANTIC_REQUEST_VERSION = 1

SEMANTIC_PROTOCOL_ID = "t11-semantic-assessment"
SEMANTIC_PROTOCOL_VERSION = 1
SEMANTIC_RUBRIC_ID = "d52-d53-lexical-assessment"
SEMANTIC_RUBRIC_VERSION = 1
SEMANTIC_PROMPT_ID = "t11-semantic-bridge"
SEMANTIC_PROMPT_VERSION = 1

SEMANTIC_PROTOCOL_TEXT_V1 = """T11 SEMANTIC ASSESSMENT PROTOCOL, version 1.

ROLE

You are the semantic assessor for one vocabulary assessment item. Judge only the supplied semantic request and only the target lexical Unit described in its unit block.

The unit block is authoritative for the target Unit. definition_en specifies the intended sense for this assessment. Do not substitute another sense of the same surface form.

CHANNEL CONSTRUCT

R assesses contextual lexical comprehension from written evidence.

L assesses contextual lexical comprehension from a spoken stimulus. The spoken_script supplied to you represents the content the learner heard; ordinary learner-facing listening did not expose that script as written text.

W assesses productive written lexical use after target presence has already been established by the deterministic presence gate.

S assesses productive lexical use from a human-approved transcript after trustworthy transcription and target presence have already been established.

For S, assess only lexical use represented by the approved transcript. Do not assess pronunciation, accent, prosody, phonetic accuracy, acoustic quality, or audio quality.

LEXICAL DECISION RULE

PASS or FAIL requires sufficient trustworthy evidence about the target Unit in the channel being assessed.

Presence of the target Unit is not evidence of correctness.

FAIL requires evidence of an applicable closed lexical error for the target Unit. Task noncompliance, off-topic content, refusal, unrelated content, failure to follow directions, weak effort, brevity, or general language errors do not by themselves establish lexical FAIL.

Errors unrelated to the target Unit do not by themselves cause FAIL.

If the supplied evidence does not support a trustworthy lexical PASS or FAIL, use an allowed ABSTAIN reason instead of guessing.

TARGET PRESENCE

For W and S, target presence was decided before this request was constructed. Do not judge target presence and never emit OMITTED.

R and L do not require the learner response to repeat the target Unit. A correct contextual paraphrase or interpretation may PASS without target repetition.

INFORMATION AND HISTORY BOUNDARY

Use only the information supplied with this request.

Do not claim or infer session identity, attempt identity, novelty, previous exposure, mastery, forgetting, progress, scheduling state, lifecycle state, or historical learner behavior.

Do not create EventLog, Anki, producer, provenance, stimulus-reference, response-reference, or lifecycle fields.

OUTPUT AUTHORITY

Your output is only a semantic proposal. A human reviewer will later APPROVE or REJECT that exact proposal.

Do not approve or reject your own proposal.

Do not state or invent your model identity, model version, provenance, confidence score, probability, band score, timestamp, session identifier, or attempt identifier.

Return only the JSON object required by prompt.text."""

SEMANTIC_RUBRIC_TEXT_V1 = """T11 LEXICAL ASSESSMENT RUBRIC, version 1.

OUTCOME RULE

PASS means sufficient trustworthy evidence demonstrates success for the exact channel construct.

FAIL means sufficient trustworthy evidence establishes a genuine lexical error involving the target Unit.

ABSTAIN means the evidence does not support a trustworthy lexical PASS or FAIL decision.

The semantic assessor may never emit OMITTED.

READING — R

Evidence:
passage, question, learner_response.

PASS when the learner_response demonstrates the contextual meaning of the target Unit in the passage consistently with definition_en.

The learner does not need to repeat the target Unit.

FAIL/wrong_meaning only when the learner_response provides trustworthy evidence that the learner attributes an incorrect lexical meaning to the target Unit in the assessed context.

Do not use wrong_meaning merely because the response is incomplete, off topic, poorly written, or fails another task requirement.

LISTENING — L

Evidence:
spoken_script, question, learner_response.

spoken_script represents the content heard by the learner. Do not treat written reproduction, spelling, transcription skill, accent discrimination, or speaker discrimination as the assessed construct.

PASS when the learner_response demonstrates a correct contextual interpretation of the target Unit in the heard material.

The learner does not need to repeat or transcribe the target Unit.

FAIL/wrong_interpretation only when the learner_response provides trustworthy evidence of an incorrect interpretation of the target Unit in the heard context.

WRITING — W

Evidence:
production_prompt, semantic_constraints, learner_response.

Target presence has already been established before semantic assessment.

PASS when the learner's actual use of the target Unit is lexically acceptable for the intended sense and context: its meaning is appropriate, its target-specific collocation is acceptable, and its target-specific form or syntactic frame is acceptable where those properties are relevant.

FAIL/semantic_misuse when the actual target use expresses the wrong sense or is semantically incompatible with the intended lexical use.

Do not use semantic_misuse merely because the learner failed to follow an instruction or otherwise failed the task. Task noncompliance alone is not lexical failure.

FAIL/collocation_misuse when the intended target sense is sufficiently clear but the target Unit participates in a lexically unacceptable collocation.

FAIL/form_misuse when the intended target sense is sufficiently clear but the morphological form, grammatical form, or target-specific syntactic frame of the Unit is unacceptable for that use.

General grammar, spelling, style, organization, or content errors unrelated to the target Unit do not by themselves cause lexical FAIL.

SPEAKING — S

Evidence:
production_prompt, semantic_constraints, approved_transcript.

Target presence has already been established before semantic assessment.

Judge lexical use from approved_transcript under the same semantic, collocation, and form criteria as W.

Do not assess pronunciation, accent, prosody, phonetic accuracy, acoustic intelligibility, audio quality, STT confidence, punctuation introduced by transcription, or informal spoken syntax unless the textual evidence changes the lexical meaning or target-specific use being assessed.

MULTIPLE W/S ERROR CATEGORIES

The v1 contract defines no precedence among simultaneous W/S lexical-error categories.

If exactly one closed failure code is clearly established, use that code.

If two or more different W/S failure codes are simultaneously supported and choosing one would require inventing a precedence rule, do not choose arbitrarily. Use ABSTAIN with reason_code semantic_uncertainty.

ABSTAIN REASONS

The semantic assessor may use exactly:

off_topic
    The evidence does not address the task sufficiently to assess the target Unit, and no independent target-specific lexical PASS or FAIL is established.

insufficient_lexical_evidence
    The evidence is related to the task but does not contain enough trustworthy target-specific evidence to decide PASS or FAIL.

response_unintelligible
    The textual learner evidence cannot be interpreted reliably enough to recover a lexical meaning. For S this concerns only the approved transcript supplied to semantic assessment; it is not an audio-quality judgement.

semantic_uncertainty
    The available evidence supports materially different plausible lexical interpretations leading to different semantic decisions, or multiple different W/S failure codes are simultaneously supported where v1 defines no precedence.

Do not use ABSTAIN merely because a response is short, informal, imperfect, or stylistically weak when sufficient lexical evidence exists.

Do not use an ABSTAIN code owned by another layer.

The semantic assessor must never emit:
refusal
explicit_skip
no_response
audio_unusable
transcription_uncertain
transcription_failed
reviewer_rejected
invalid_artifact
infrastructure_failure

SEMANTIC RATIONALE

semantic_rationale is required for every proposal.

It must briefly identify the target-specific evidence that supports the chosen outcome and code.

Do not invent evidence.

Do not give a confidence score.

The deterministic importer validates presence and binding of semantic_rationale, not its truth or persuasive quality."""

SEMANTIC_PROMPT_TEXT_V1 = """Assess the single item in the supplied T11 semantic request.

Follow protocol.text and rubric.text exactly.

The human-mediated submission supplies two separate inputs:

1. REQUEST_DIGEST: the lowercase 64-hex SHA-256 digest computed locally from the complete canonical semantic-request artifact.
2. SEMANTIC_REQUEST: the semantic-request JSON object.

REQUEST_DIGEST is supplied alongside the request. It is not a field inside the semantic-request artifact.

Copy REQUEST_DIGEST exactly into the request_digest field of your semantic proposal. Do not calculate, modify, normalize, prefix, or invent the digest.

Return exactly one JSON object and nothing else.

Do not use a markdown code fence.
Do not write text before or after the JSON object.

The JSON object contains exactly these seven keys:

artifact
v
request_digest
outcome
failure_code
reason_code
semantic_rationale

Set artifact to exactly:

vocab.t11.semantic-response

Set v to the integer:

1

Set request_digest to exactly the supplied REQUEST_DIGEST.

Set outcome to exactly one of:

PASS
FAIL
ABSTAIN

Never emit OMITTED.

For PASS:

failure_code = ""
reason_code = ""

For FAIL:

failure_code = exactly one failure code allowed for the request-derived channel by rubric.text
reason_code = ""

For ABSTAIN:

failure_code = ""
reason_code = exactly one of:

off_topic
insufficient_lexical_evidence
response_unintelligible
semantic_uncertainty

failure_code and reason_code are always present as strings.

Never omit either field.
Never use null.
Never add another key.
Never add nested metadata.

semantic_rationale must be a non-whitespace string that briefly states the target-specific evidence supporting the proposal.

Do not include model identity, model version, reviewer identity, review decision, confidence, probability, timestamp, session identity, attempt identity, novelty, lifecycle state, producer fields, provenance fields, or EventLog fields."""


class SemanticRequestError(ValueError):
    """Raised when a T11 semantic request is not an exact valid v1 artifact."""


_TOP_LEVEL_FIELDS = frozenset(
    ("artifact", "v", "protocol", "rubric", "prompt", "unit", "task")
)
_INSTRUCTION_FIELDS = frozenset(("id", "version", "text"))
_UNIT_FIELDS = frozenset(("unit_key", "lemma", "unit_type", "definition_en"))
_TASK_CONTENT_FIELDS_BY_CHANNEL = {
    "R": ("passage", "question", "learner_response"),
    "L": ("spoken_script", "question", "learner_response"),
    "W": ("production_prompt", "semantic_constraints", "learner_response"),
    "S": ("production_prompt", "semantic_constraints", "approved_transcript"),
}
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def build_semantic_request(
    *,
    unit_key: str,
    lemma: str,
    unit_type: str,
    definition_en: str,
    channel: str,
    task_content: Mapping[str, str],
    protocol_text: str = SEMANTIC_PROTOCOL_TEXT_V1,
    rubric_text: str = SEMANTIC_RUBRIC_TEXT_V1,
    prompt_text: str = SEMANTIC_PROMPT_TEXT_V1,
) -> dict[str, object]:
    """Construct one validated request; W/S require prior presence gating."""
    if type(channel) is not str or channel not in CHANNELS:
        raise SemanticRequestError("channel is invalid")
    if not isinstance(task_content, Mapping):
        raise SemanticRequestError("task_content must be a mapping")
    content_fields = _TASK_CONTENT_FIELDS_BY_CHANNEL[channel]
    if set(task_content) != set(content_fields):
        raise SemanticRequestError("task_content has the wrong key set")

    request = {
        "artifact": SEMANTIC_REQUEST_ARTIFACT,
        "v": SEMANTIC_REQUEST_VERSION,
        "protocol": {
            "id": SEMANTIC_PROTOCOL_ID,
            "version": SEMANTIC_PROTOCOL_VERSION,
            "text": protocol_text,
        },
        "rubric": {
            "id": SEMANTIC_RUBRIC_ID,
            "version": SEMANTIC_RUBRIC_VERSION,
            "text": rubric_text,
        },
        "prompt": {
            "id": SEMANTIC_PROMPT_ID,
            "version": SEMANTIC_PROMPT_VERSION,
            "text": prompt_text,
        },
        "unit": {
            "unit_key": unit_key,
            "lemma": lemma,
            "unit_type": unit_type,
            "definition_en": definition_en,
        },
        "task": {
            "channel": channel,
            "task_kind": ASSESSMENT_TASK_KIND_BY_CHANNEL[channel],
            **{field: task_content[field] for field in content_fields},
        },
    }
    return _validated_semantic_request(request)


def import_semantic_request(raw: bytes) -> dict[str, object]:
    """Strictly import and validate one semantic-request artifact."""
    try:
        value = strict_json_loads(raw)
    except ArtifactJSONError as exc:
        raise SemanticRequestError(str(exc)) from None
    return _validated_semantic_request(value)


def serialize_semantic_request(request: object) -> bytes:
    """Return canonical bytes for one complete validated request."""
    return canonical_json_bytes(_validated_semantic_request(request))


def semantic_request_digest(request: object) -> str:
    """Return the bare full lowercase digest of one validated request."""
    return canonical_sha256(_validated_semantic_request(request))


def prepare_semantic_request_submission(request: object) -> tuple[bytes, str]:
    """Return canonical request bytes and its external submission digest."""
    validated = _validated_semantic_request(request)
    return canonical_json_bytes(validated), canonical_sha256(validated)


def _validated_semantic_request(value: object) -> dict[str, object]:
    request = _require_mapping(value, "semantic request")
    _require_keys(request, _TOP_LEVEL_FIELDS, "semantic request")

    artifact = _require_text(request["artifact"], "artifact")
    if artifact != SEMANTIC_REQUEST_ARTIFACT:
        raise SemanticRequestError("artifact discriminator is invalid")
    version = request["v"]
    if type(version) is not int or version != SEMANTIC_REQUEST_VERSION:
        raise SemanticRequestError("artifact version is invalid")

    protocol = _validated_instruction(
        request["protocol"],
        name="protocol",
        expected_id=SEMANTIC_PROTOCOL_ID,
        expected_version=SEMANTIC_PROTOCOL_VERSION,
    )
    rubric = _validated_instruction(
        request["rubric"],
        name="rubric",
        expected_id=SEMANTIC_RUBRIC_ID,
        expected_version=SEMANTIC_RUBRIC_VERSION,
    )
    prompt = _validated_instruction(
        request["prompt"],
        name="prompt",
        expected_id=SEMANTIC_PROMPT_ID,
        expected_version=SEMANTIC_PROMPT_VERSION,
    )
    unit = _validated_unit(request["unit"])
    task = _validated_task(request["task"])

    return {
        "artifact": artifact,
        "v": version,
        "protocol": protocol,
        "rubric": rubric,
        "prompt": prompt,
        "unit": unit,
        "task": task,
    }


def _validated_instruction(
    value: object,
    *,
    name: str,
    expected_id: str,
    expected_version: int,
) -> dict[str, object]:
    instruction = _require_mapping(value, name)
    _require_keys(instruction, _INSTRUCTION_FIELDS, name)
    instruction_id = _require_text(instruction["id"], f"{name}.id")
    if instruction_id != expected_id:
        raise SemanticRequestError(f"{name}.id is invalid")
    version = instruction["version"]
    if type(version) is not int or version != expected_version:
        raise SemanticRequestError(f"{name}.version is invalid")
    text = _require_text(instruction["text"], f"{name}.text")
    return {"id": instruction_id, "version": version, "text": text}


def _validated_unit(value: object) -> dict[str, str]:
    unit = _require_mapping(value, "unit")
    _require_keys(unit, _UNIT_FIELDS, "unit")
    unit_key = _require_text(unit["unit_key"], "unit.unit_key")
    if _UNIT_KEY_RE.fullmatch(unit_key) is None:
        raise SemanticRequestError("unit.unit_key is invalid")
    lemma = _require_text(unit["lemma"], "unit.lemma")
    unit_type = _require_text(unit["unit_type"], "unit.unit_type")
    if unit_type not in UNIT_TYPE_VALUES:
        raise SemanticRequestError("unit.unit_type is invalid")
    definition_en = _require_text(unit["definition_en"], "unit.definition_en")
    return {
        "unit_key": unit_key,
        "lemma": lemma,
        "unit_type": unit_type,
        "definition_en": definition_en,
    }


def _validated_task(value: object) -> dict[str, str]:
    task = _require_mapping(value, "task")
    channel = _require_text(task.get("channel"), "task.channel")
    if channel not in CHANNELS:
        raise SemanticRequestError("task.channel is invalid")
    content_fields = _TASK_CONTENT_FIELDS_BY_CHANNEL[channel]
    expected_fields = frozenset(("channel", "task_kind", *content_fields))
    _require_keys(task, expected_fields, "task")
    task_kind = _require_text(task["task_kind"], "task.task_kind")
    if task_kind != ASSESSMENT_TASK_KIND_BY_CHANNEL[channel]:
        raise SemanticRequestError("task channel/task_kind pair is invalid")
    content = {
        field: _require_text(task[field], f"task.{field}")
        for field in content_fields
    }
    return {"channel": channel, "task_kind": task_kind, **content}


def _require_mapping(value: object, name: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise SemanticRequestError(f"{name} must be an object")
    return value


def _require_keys(
    value: Mapping[object, object],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise SemanticRequestError(f"{name} has the wrong key set")


def _require_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise SemanticRequestError(f"{name} must be a non-whitespace string")
    if _SURROGATE_RE.search(value) is not None:
        raise SemanticRequestError(f"{name} contains an unpaired surrogate")
    return value
