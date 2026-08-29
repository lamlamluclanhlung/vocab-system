"""Human-mediated two-phase Forge bridge, frozen by D70 section 17.

Nothing here calls a model. Export writes a request artifact, a human performs
the generation externally, and import replays the saved response into the
existing Forge core. No provider SDK, no HTTP, no default model identity.

The bridge owns transport and binding only. Schema conformance, validators,
duplicate handling, preview, commit intent, and the Anki write all remain with
forge().
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from ..contracts import EVENT_LOCAL_TIMEZONE
from ..forge.request import (
    ConfirmationDecision,
    ForgePreview,
    ForgeRequest,
    GenerationMetadata,
)
from ..forge.schema import FORGE_JSON_SCHEMA
from .errors import RuntimeForgeBridgeError


REQUEST_ARTIFACT = "vocab.forge.request"
RESPONSE_ARTIFACT = "vocab.forge.response"
ARTIFACT_VERSION = 1

FORGE_PROMPT_VERSION = "forge-v1"
FORGE_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "forge-v1.md"

REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "artifact",
        "artifact_version",
        "source_ref",
        "source_sentence",
        "learner_note",
        "generation_request_sha256",
        "prompt_version",
        "prompt_sha256",
        "prompt_text",
        "json_schema",
        "generation_config",
    }
)

RESPONSE_KEYS: frozenset[str] = frozenset(
    {
        "artifact",
        "artifact_version",
        "generation_request_sha256",
        "model_id",
        "model_version",
        "structured_output",
    }
)


def local_timezone() -> timezone | ZoneInfo:
    """Resolve the project calendar zone, mirroring the journal's fallback.

    The journal records ``day`` in EVENT_LOCAL_TIMEZONE and falls back to the
    contemporary fixed UTC+07:00 offset where IANA data is unavailable, which is
    the common case on Windows. A Unit's ``created`` date must sit on the same
    human calendar day, so the same resolution is repeated here rather than
    importing the journal module, which the D69 section 10 allowlist forbids.

    The host's configured local timezone is deliberately never consulted.
    """
    try:
        return ZoneInfo(EVENT_LOCAL_TIMEZONE)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=7), EVENT_LOCAL_TIMEZONE)


def local_day(instant: datetime | None = None) -> date:
    """Return the project-local calendar day of one UTC instant."""
    moment = datetime.now(timezone.utc) if instant is None else instant
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise RuntimeForgeBridgeError("clock returned a naive datetime")
    return moment.astimezone(local_timezone()).date()


@dataclass(frozen=True, slots=True)
class ForgePrompt:
    """The repo-owned prompt artifact, hashed from its bytes at run time."""

    version: str
    sha256: str
    text: str


def load_prompt() -> ForgePrompt:
    """Read and hash the one repo-owned prompt. No fallback, no override."""
    try:
        raw = FORGE_PROMPT_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeForgeBridgeError(
            f"forge prompt artifact could not be read: {exc}"
        ) from exc
    if not raw:
        raise RuntimeForgeBridgeError("forge prompt artifact is empty")
    return ForgePrompt(
        version=FORGE_PROMPT_VERSION,
        sha256=hashlib.sha256(raw).hexdigest(),
        text=raw.decode("utf-8"),
    )


def generation_request_sha256(request: ForgeRequest) -> str:
    """Recompute the Forge core's own request identity.

    This mirrors vocab.forge.event_payloads.build_provenance exactly. It hashes
    only the three ForgeRequest fields. The prompt, the schema, the model, and
    the artifact version are deliberately excluded: adding any of them would
    create a second competing identity that no longer matches the provenance
    the core records.
    """
    return canonical_sha256(
        {
            "source_ref": request.source_ref,
            "source_sentence": request.source_sentence,
            "learner_note": request.learner_note,
        }
    )


def _closed_object(raw: object, keys: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise RuntimeForgeBridgeError(f"{label} must be a JSON object")
    present = set(raw)
    unknown = sorted(present - keys)
    if unknown:
        raise RuntimeForgeBridgeError(f"{label} has unknown keys: {unknown}")
    missing = sorted(keys - present)
    if missing:
        raise RuntimeForgeBridgeError(f"{label} is missing keys: {missing}")
    return dict(raw)


def _required_text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw[key]
    if type(value) is not str or not value.strip():
        raise RuntimeForgeBridgeError(f"{label}.{key} must be a non-empty string")
    return value


def _required_sha256(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw[key]
    if type(value) is not str or len(value) != 64:
        raise RuntimeForgeBridgeError(
            f"{label}.{key} must be 64 lowercase hexadecimal characters"
        )
    if any(character not in "0123456789abcdef" for character in value):
        raise RuntimeForgeBridgeError(
            f"{label}.{key} must be 64 lowercase hexadecimal characters"
        )
    return value


def _discriminator(raw: Mapping[str, object], expected: str, label: str) -> None:
    if raw["artifact"] != expected:
        raise RuntimeForgeBridgeError(f"{label}.artifact must be {expected!r}")
    version = raw["artifact_version"]
    if type(version) is not int or version != ARTIFACT_VERSION:
        raise RuntimeForgeBridgeError(
            f"{label}.artifact_version must be exactly {ARTIFACT_VERSION}"
        )


def build_request_artifact(request: ForgeRequest, prompt: ForgePrompt) -> bytes:
    """Render one deterministic request artifact for manual generation."""
    if type(request) is not ForgeRequest:
        raise RuntimeForgeBridgeError("request must be a ForgeRequest")
    for field_name in ("source_ref", "source_sentence", "learner_note"):
        if type(getattr(request, field_name)) is not str:
            raise RuntimeForgeBridgeError(f"{field_name} must be a string")
    body = {
        "artifact": REQUEST_ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "source_ref": request.source_ref,
        "source_sentence": request.source_sentence,
        "learner_note": request.learner_note,
        "generation_request_sha256": generation_request_sha256(request),
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "prompt_text": prompt.text,
        "json_schema": copy.deepcopy(FORGE_JSON_SCHEMA),
        "generation_config": {},
    }
    try:
        return canonical_json_bytes(body)
    except ArtifactJSONError as exc:
        raise RuntimeForgeBridgeError(
            f"request artifact is not canonical JSON: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ForgeRequestArtifact:
    """One strict-decoded, self-consistent request artifact."""

    request: ForgeRequest
    generation_request_sha256: str
    prompt_version: str
    prompt_sha256: str
    prompt_text: str


def parse_request_artifact(raw_bytes: bytes) -> ForgeRequestArtifact:
    """Strict-decode and fully validate one request artifact."""
    label = "forge request artifact"
    try:
        decoded = strict_json_loads(raw_bytes)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeForgeBridgeError(f"{label} is not strict JSON: {exc}") from exc

    body = _closed_object(decoded, REQUEST_KEYS, label)
    _discriminator(body, REQUEST_ARTIFACT, label)

    for field_name in ("source_ref", "source_sentence", "learner_note"):
        if type(body[field_name]) is not str:
            raise RuntimeForgeBridgeError(f"{label}.{field_name} must be a string")
    request = ForgeRequest(
        source_ref=body["source_ref"],
        source_sentence=body["source_sentence"],
        learner_note=body["learner_note"],
    )

    recorded = _required_sha256(body, "generation_request_sha256", label)
    if recorded != generation_request_sha256(request):
        raise RuntimeForgeBridgeError(
            f"{label}.generation_request_sha256 does not match its own fields"
        )

    if body["json_schema"] != FORGE_JSON_SCHEMA:
        raise RuntimeForgeBridgeError(f"{label}.json_schema is not FORGE_JSON_SCHEMA")
    if body["generation_config"] != {}:
        raise RuntimeForgeBridgeError(f"{label}.generation_config must be empty")

    return ForgeRequestArtifact(
        request=request,
        generation_request_sha256=recorded,
        prompt_version=_required_text(body, "prompt_version", label),
        prompt_sha256=_required_sha256(body, "prompt_sha256", label),
        prompt_text=_required_text(body, "prompt_text", label),
    )


@dataclass(frozen=True, slots=True)
class ForgeResponseArtifact:
    """One strict-decoded response artifact, not yet bound to a request."""

    generation_request_sha256: str
    model_id: str
    model_version: str
    structured_output: Mapping[str, object]


def parse_response_artifact(raw_bytes: bytes) -> ForgeResponseArtifact:
    """Strict-decode and fully validate one response artifact."""
    label = "forge response artifact"
    try:
        decoded = strict_json_loads(raw_bytes)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeForgeBridgeError(f"{label} is not strict JSON: {exc}") from exc

    body = _closed_object(decoded, RESPONSE_KEYS, label)
    _discriminator(body, RESPONSE_ARTIFACT, label)

    output = body["structured_output"]
    if not isinstance(output, dict):
        raise RuntimeForgeBridgeError(f"{label}.structured_output must be an object")

    return ForgeResponseArtifact(
        generation_request_sha256=_required_sha256(
            body, "generation_request_sha256", label
        ),
        model_id=_required_text(body, "model_id", label),
        model_version=_required_text(body, "model_version", label),
        structured_output=copy.deepcopy(output),
    )


@dataclass(frozen=True, slots=True)
class BoundGeneration:
    """A request and response proven to belong together, ready for forge()."""

    request: ForgeRequest
    metadata: GenerationMetadata
    structured_output: Mapping[str, object]


def bind_generation(
    request_artifact: ForgeRequestArtifact,
    response_artifact: ForgeResponseArtifact,
    prompt: ForgePrompt,
) -> BoundGeneration:
    """Prove the response answers this request under the current prompt.

    Three bindings must hold. The response must carry the request's identity;
    that identity must equal the identity recomputed from the ForgeRequest
    fields; and the prompt on disk must still be the prompt the request was
    exported under; and the prompt text carried in the request must be that
    same prompt verbatim. The last two are what stop a new prompt identity from
    being attached to an old generation, and an edited prompt from being
    laundered under the repo prompt's provenance.
    """
    recomputed = generation_request_sha256(request_artifact.request)
    if request_artifact.generation_request_sha256 != recomputed:
        raise RuntimeForgeBridgeError("request artifact identity is inconsistent")
    if response_artifact.generation_request_sha256 != recomputed:
        raise RuntimeForgeBridgeError(
            "response does not answer this request: "
            "generation_request_sha256 mismatch"
        )
    if request_artifact.prompt_version != prompt.version:
        raise RuntimeForgeBridgeError(
            "prompt version has changed since this request was exported; "
            "re-export the request rather than reusing the old generation"
        )
    if request_artifact.prompt_sha256 != prompt.sha256:
        raise RuntimeForgeBridgeError(
            "prompt artifact has changed since this request was exported; "
            "re-export the request rather than reusing the old generation"
        )
    # The identity checks above prove the repo prompt is unchanged. This proves
    # the text the human actually generated against is that same prompt, so an
    # edited prompt_text cannot be laundered under the repo prompt's provenance.
    # Compared exactly: no stripping, no whitespace or newline normalization.
    if request_artifact.prompt_text != prompt.text:
        raise RuntimeForgeBridgeError(
            "request prompt_text is not the repo-owned prompt for "
            f"{prompt.version}; the carried prompt was edited"
        )

    metadata = GenerationMetadata(
        model_id=response_artifact.model_id,
        model_version=response_artifact.model_version,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        generation_config={},
    )
    return BoundGeneration(
        request=request_artifact.request,
        metadata=metadata,
        structured_output=copy.deepcopy(dict(response_artifact.structured_output)),
    )


class ReplayGenerator:
    """Replay one already-bound generation through the Generator protocol.

    The adapter reads no file, parses no JSON, performs no network call, and
    invents no metadata. It is constructed only from an already strict-decoded,
    request-bound, prompt-bound generation, and it re-verifies that binding
    against the arguments forge() actually passes it.
    """

    def __init__(self, bound: BoundGeneration) -> None:
        if type(bound) is not BoundGeneration:
            raise RuntimeForgeBridgeError("replay requires a bound generation")
        self._request = bound.request
        self._metadata = bound.metadata
        self._structured_output = copy.deepcopy(dict(bound.structured_output))

    def generate(
        self,
        request: ForgeRequest,
        *,
        json_schema: Mapping[str, object],
        metadata: GenerationMetadata,
    ) -> Mapping[str, object]:
        if request != self._request:
            raise RuntimeForgeBridgeError(
                "replay was asked to answer a different ForgeRequest"
            )
        if json_schema != FORGE_JSON_SCHEMA:
            raise RuntimeForgeBridgeError("replay was given a foreign JSON schema")
        if metadata != self._metadata:
            raise RuntimeForgeBridgeError(
                "replay was given foreign generation metadata"
            )
        return copy.deepcopy(self._structured_output)


class TerminalConfirmation:
    """A real human confirmation port. Declining is the default."""

    def __init__(
        self,
        actor_id: str,
        *,
        stream_in: TextIO,
        stream_out: TextIO,
    ) -> None:
        if type(actor_id) is not str or not actor_id.strip():
            raise RuntimeForgeBridgeError("actor id must be an explicit non-empty string")
        self.actor_id = actor_id
        self._in = stream_in
        self._out = stream_out

    def decide(self, preview: ForgePreview) -> ConfirmationDecision:
        self._out.write(render_preview(preview))
        self._out.write(f"\nCommit this Unit as {self.actor_id}? [y/N] ")
        self._out.flush()
        answer = self._in.readline()
        confirmed = answer.strip().lower() in {"y", "yes"}
        return ConfirmationDecision(confirmed=confirmed, actor_id=self.actor_id)


def render_preview(preview: ForgePreview) -> str:
    """Render one ForgePreview for human review."""
    lines = [
        "",
        f"  unit_key       {preview.unit_key}",
        f"  lemma          {preview.lemma}",
        f"  unit_type      {preview.unit_type}",
        f"  register       {preview.register}",
        f"  definition_en  {preview.definition_en}",
        f"  source_ref     {preview.source_ref}",
        f"  source         {preview.source_sentence}",
        f"  targets        {', '.join(preview.targets) or '(none)'}",
    ]
    for channel, state in preview.states:
        lines.append(f"  state_{channel}        {state}")
    for channel, justification in preview.target_justification:
        lines.append(f"  why {channel}          {justification}")
    return "\n".join(lines) + "\n"
