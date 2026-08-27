"""Immutable T12 assessment-session manifests and content identities."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .artifact_json import ArtifactJSONError, canonical_json_bytes, strict_json_loads
from .assessment_identity import (
    SESSION_ID_PATTERN,
    AssessmentIdentityError,
    cognitive_stimulus_ref,
)
from .contracts import (
    ASSESSMENT_ARTIFACT_REF_PATTERN,
    ASSESSMENT_TASK_KIND_BY_CHANNEL,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    UNIT_KEY_PATTERN,
)


SESSION_MANIFEST_ARTIFACT = "vocab.t12.session-manifest"
SESSION_MANIFEST_VERSION = 1
SESSION_NONCE_PATTERN = r"^[0-9a-f]{64}$"

_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_SESSION_NONCE_RE = re.compile(SESSION_NONCE_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_TOP_LEVEL_FIELDS = frozenset(
    (
        "artifact",
        "v",
        "session_nonce",
        "created_at",
        "producer",
        "producer_version",
        "items",
    )
)
_ITEM_FIELDS = frozenset(
    (
        "item_ordinal",
        "unit_key",
        "channel",
        "task_kind",
        "stimulus",
        "presented_stimulus_ref",
        "stimulus_artifact_ref",
    )
)
_STIMULUS_FIELDS_BY_CHANNEL = {
    "R": frozenset(("passage", "question")),
    "L": frozenset(("spoken_script", "question")),
    "W": frozenset(("production_prompt", "semantic_constraints")),
    "S": frozenset(("production_prompt", "semantic_constraints")),
}


class SessionManifestError(ValueError):
    """Raised when a T12 session manifest or stored identity is invalid."""


@dataclass(frozen=True, slots=True)
class SessionManifest:
    """One validated immutable manifest represented by canonical bytes."""

    session_id: str
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, object]:
        """Return a detached mutable view without exposing internal state."""
        value = strict_json_loads(self.canonical_bytes)
        if type(value) is not dict:  # pragma: no cover - guaranteed on import
            raise AssertionError("validated session manifest is not an object")
        return value


def create_session_manifest(
    *,
    created_at: object,
    items: object,
) -> SessionManifest:
    """Allocate a fresh nonce and build one immutable session manifest."""
    manifest = {
        "artifact": SESSION_MANIFEST_ARTIFACT,
        "v": SESSION_MANIFEST_VERSION,
        "session_nonce": secrets.token_hex(32),
        "created_at": created_at,
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "items": items,
    }
    try:
        raw = canonical_json_bytes(manifest)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SessionManifestError("session manifest is not canonical-JSON serializable") from exc
    return import_session_manifest(raw)


def import_session_manifest(raw: bytes) -> SessionManifest:
    """Strictly import, close, and independently verify one manifest."""
    try:
        value = strict_json_loads(raw)
    except (ArtifactJSONError, TypeError) as exc:
        raise SessionManifestError(str(exc)) from None
    validated = _validated_manifest(value)
    try:
        canonical = canonical_json_bytes(validated)
    except (TypeError, ValueError, UnicodeError) as exc:  # pragma: no cover
        raise SessionManifestError("session manifest cannot be serialized") from exc
    digest = hashlib.sha256(canonical).hexdigest()
    return SessionManifest(
        session_id=f"session:v1:{digest}",
        canonical_bytes=canonical,
    )


def serialize_session_manifest(manifest: SessionManifest) -> bytes:
    """Return canonical bytes after revalidating the immutable value."""
    validated = _validated_manifest_object(manifest)
    return validated.canonical_bytes


def persist_session_manifest(
    root: str | os.PathLike[str],
    manifest: SessionManifest,
) -> Path:
    """Durably publish a manifest under the digest suffix of its session id."""
    validated = _validated_manifest_object(manifest)
    if root is None:
        raise TypeError("session manifest root must be explicit")
    root_path = Path(root)
    if not root_path.name:
        raise ValueError("session manifest root must identify a directory")
    if root_path.exists() and not root_path.is_dir():
        raise SessionManifestError("session manifest root is not a directory")
    root_path.mkdir(parents=True, exist_ok=True)
    suffix = validated.session_id.removeprefix("session:v1:")
    path = root_path / suffix
    expected = validated.canonical_bytes

    if path.exists():
        _verify_persisted_manifest(path, validated.session_id, expected)
        return path

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{suffix}.",
        suffix=".tmp",
        dir=root_path,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            pass
        _verify_persisted_manifest(path, validated.session_id, expected)
    except OSError as exc:
        raise SessionManifestError(
            f"session manifest could not be durably published: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return path


def load_session_manifest(
    root: str | os.PathLike[str],
    session_id: object,
) -> SessionManifest:
    """Load one exact manifest by session id without directory discovery."""
    if type(session_id) is not str or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise SessionManifestError("session_id is invalid")
    if root is None:
        raise TypeError("session manifest root must be explicit")
    path = Path(root) / session_id.removeprefix("session:v1:")
    try:
        raw = path.read_bytes()
    except (OSError, FileNotFoundError, IsADirectoryError) as exc:
        raise SessionManifestError("session manifest is missing or unreadable") from exc
    imported = import_session_manifest(raw)
    if imported.session_id != session_id:
        raise SessionManifestError("stored manifest does not match requested session_id")
    if raw != imported.canonical_bytes:
        raise SessionManifestError("stored manifest bytes are not canonical")
    return imported


def _validated_manifest(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _TOP_LEVEL_FIELDS:
        raise SessionManifestError("session manifest has the wrong top-level key set")
    if value["artifact"] != SESSION_MANIFEST_ARTIFACT or type(value["artifact"]) is not str:
        raise SessionManifestError("session manifest artifact discriminator is invalid")
    if type(value["v"]) is not int or value["v"] != SESSION_MANIFEST_VERSION:
        raise SessionManifestError("session manifest version is invalid")
    nonce = value["session_nonce"]
    if type(nonce) is not str or _SESSION_NONCE_RE.fullmatch(nonce) is None:
        raise SessionManifestError("session_nonce is invalid")
    _validated_utc_timestamp(value["created_at"], "created_at")
    if (
        type(value["producer"]) is not str
        or value["producer"] != T12_ASSESSMENT_PRODUCER_ID
    ):
        raise SessionManifestError("session manifest producer is invalid")
    if (
        type(value["producer_version"]) is not int
        or value["producer_version"] != T12_ASSESSMENT_PRODUCER_VERSION
    ):
        raise SessionManifestError("session manifest producer_version is invalid")
    items = value["items"]
    if type(items) is not list:
        raise SessionManifestError("session manifest items must be an array")

    validated_items: list[dict[str, object]] = []
    previous_ordinal: int | None = None
    for item in items:
        validated_item = _validated_item(item)
        ordinal = validated_item["item_ordinal"]
        if previous_ordinal is not None and ordinal <= previous_ordinal:
            raise SessionManifestError(
                "session item ordinals must be unique and strictly ascending"
            )
        previous_ordinal = ordinal
        validated_items.append(validated_item)
    return {
        "artifact": SESSION_MANIFEST_ARTIFACT,
        "v": SESSION_MANIFEST_VERSION,
        "session_nonce": nonce,
        "created_at": value["created_at"],
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "items": validated_items,
    }


def _validated_item(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _ITEM_FIELDS:
        raise SessionManifestError("session manifest item has the wrong key set")
    ordinal = value["item_ordinal"]
    if type(ordinal) is not int or ordinal < 0:
        raise SessionManifestError(
            "item_ordinal must be an actual non-negative integer"
        )
    unit_key = value["unit_key"]
    if type(unit_key) is not str or _UNIT_KEY_RE.fullmatch(unit_key) is None:
        raise SessionManifestError("session item unit_key is invalid")
    channel = value["channel"]
    if type(channel) is not str or channel not in ASSESSMENT_TASK_KIND_BY_CHANNEL:
        raise SessionManifestError("session item channel is invalid")
    task_kind = value["task_kind"]
    if (
        type(task_kind) is not str
        or task_kind != ASSESSMENT_TASK_KIND_BY_CHANNEL[channel]
    ):
        raise SessionManifestError("session item task_kind does not match channel")
    stimulus = value["stimulus"]
    expected_stimulus_fields = _STIMULUS_FIELDS_BY_CHANNEL[channel]
    if type(stimulus) is not dict or set(stimulus) != expected_stimulus_fields:
        raise SessionManifestError("session item stimulus has the wrong key set")
    validated_stimulus: dict[str, str] = {}
    for name in sorted(expected_stimulus_fields):
        text = stimulus[name]
        if type(text) is not str or not text.strip():
            raise SessionManifestError(f"stimulus {name} must be non-whitespace")
        if _SURROGATE_RE.search(text) is not None:
            raise SessionManifestError(f"stimulus {name} contains an unpaired surrogate")
        validated_stimulus[name] = text
    presented_ref = value["presented_stimulus_ref"]
    try:
        expected_ref = cognitive_stimulus_ref(
            unit_key=unit_key,
            channel=channel,
            task_kind=task_kind,
            stimulus=validated_stimulus,
        )
    except AssessmentIdentityError as exc:  # pragma: no cover - inputs checked above
        raise SessionManifestError(str(exc)) from exc
    if type(presented_ref) is not str or presented_ref != expected_ref:
        raise SessionManifestError(
            "presented_stimulus_ref does not match the derived cognitive identity"
        )
    artifact_ref = value["stimulus_artifact_ref"]
    if type(artifact_ref) is not str or _ARTIFACT_REF_RE.fullmatch(artifact_ref) is None:
        raise SessionManifestError("stimulus_artifact_ref is invalid")
    return {
        "item_ordinal": ordinal,
        "unit_key": unit_key,
        "channel": channel,
        "task_kind": task_kind,
        "stimulus": validated_stimulus,
        "presented_stimulus_ref": presented_ref,
        "stimulus_artifact_ref": artifact_ref,
    }


def _validated_manifest_object(manifest: object) -> SessionManifest:
    if not isinstance(manifest, SessionManifest):
        raise TypeError("manifest must be a SessionManifest")
    imported = import_session_manifest(manifest.canonical_bytes)
    if imported.session_id != manifest.session_id:
        raise SessionManifestError("SessionManifest identity does not match its bytes")
    return imported


def _verify_persisted_manifest(
    path: Path,
    session_id: str,
    expected: bytes,
) -> None:
    try:
        raw = path.read_bytes()
    except (OSError, FileNotFoundError, IsADirectoryError) as exc:
        raise SessionManifestError("persisted session manifest is unreadable") from exc
    if raw != expected:
        raise SessionManifestError("persisted session manifest bytes conflict")
    imported = import_session_manifest(raw)
    if imported.session_id != session_id:
        raise SessionManifestError("persisted session manifest identity conflicts")


def _validated_utc_timestamp(value: object, name: str) -> str:
    from datetime import datetime, timezone

    if type(value) is not str or not value:
        raise SessionManifestError(f"{name} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SessionManifestError(f"{name} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SessionManifestError(f"{name} must use explicit UTC +00:00")
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise SessionManifestError(f"{name} must be normalized UTC with +00:00")
    return value
