"""Runtime identity schema and commit-marker publication (D70 s4, s5)."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..artifact_json import (
    ArtifactJSONError,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from ..t12_jsonl import validated_utc_timestamp
from .errors import RuntimeIdentityError
from .layout import DURABLE_LAYOUT_VERSION


IDENTITY_VERSION = 1

IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "identity_version",
        "runtime_id",
        "layout_version",
        "created_utc",
        "bootstrap_registry_count",
        "bootstrap_registry_digest",
    }
)

_RUNTIME_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)

_DIGEST_PREFIX = "sha256:"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """The commit marker of one deployment, validated against D70 s4."""

    identity_version: int
    runtime_id: str
    layout_version: int
    created_utc: str
    bootstrap_registry_count: int
    bootstrap_registry_digest: str


def registry_digest(unit_keys: Sequence[str]) -> str:
    """Return the frozen bootstrap registry digest over unit keys alone."""
    projection = list(unit_keys)
    for value in projection:
        if type(value) is not str:
            raise RuntimeIdentityError("registry projection must be strings")
    if projection != sorted(projection):
        raise RuntimeIdentityError(
            "registry projection must be ascending by unit_key"
        )
    if len(set(projection)) != len(projection):
        raise RuntimeIdentityError("registry projection must be unique")
    return f"{_DIGEST_PREFIX}{canonical_sha256(projection)}"


def identity_mapping(identity: RuntimeIdentity) -> dict[str, object]:
    """Return the exact JSON object body of one runtime identity."""
    return {
        "identity_version": identity.identity_version,
        "runtime_id": identity.runtime_id,
        "layout_version": identity.layout_version,
        "created_utc": identity.created_utc,
        "bootstrap_registry_count": identity.bootstrap_registry_count,
        "bootstrap_registry_digest": identity.bootstrap_registry_digest,
    }


def validated_identity_mapping(raw: object) -> RuntimeIdentity:
    """Validate one decoded identity object against the closed D70 schema."""
    if not isinstance(raw, dict):
        raise RuntimeIdentityError("runtime identity must be a JSON object")
    keys = set(raw)
    unknown = sorted(keys - IDENTITY_KEYS)
    if unknown:
        raise RuntimeIdentityError(f"runtime identity has unknown keys: {unknown}")
    missing = sorted(IDENTITY_KEYS - keys)
    if missing:
        raise RuntimeIdentityError(f"runtime identity is missing keys: {missing}")

    identity_version = raw["identity_version"]
    if type(identity_version) is not int or identity_version != IDENTITY_VERSION:
        raise RuntimeIdentityError(
            f"identity_version must be exactly {IDENTITY_VERSION}"
        )

    runtime_id = raw["runtime_id"]
    if type(runtime_id) is not str or _RUNTIME_ID_RE.fullmatch(runtime_id) is None:
        raise RuntimeIdentityError("runtime_id must be a canonical UUID version 4")

    layout_version = raw["layout_version"]
    if type(layout_version) is not int or layout_version != DURABLE_LAYOUT_VERSION:
        raise RuntimeIdentityError(
            f"layout_version must be exactly {DURABLE_LAYOUT_VERSION}"
        )

    created_utc = validated_utc_timestamp(
        raw["created_utc"], "created_utc", RuntimeIdentityError
    )

    count = raw["bootstrap_registry_count"]
    if type(count) is not int or count < 0:
        raise RuntimeIdentityError(
            "bootstrap_registry_count must be a non-negative integer"
        )

    digest = raw["bootstrap_registry_digest"]
    if type(digest) is not str or not digest.startswith(_DIGEST_PREFIX):
        raise RuntimeIdentityError(
            "bootstrap_registry_digest must carry the sha256: prefix"
        )
    if _DIGEST_RE.fullmatch(digest[len(_DIGEST_PREFIX) :]) is None:
        raise RuntimeIdentityError(
            "bootstrap_registry_digest must be 64 lowercase hexadecimal digits"
        )

    return RuntimeIdentity(
        identity_version=identity_version,
        runtime_id=runtime_id,
        layout_version=layout_version,
        created_utc=created_utc,
        bootstrap_registry_count=count,
        bootstrap_registry_digest=digest,
    )


def read_identity(path: object) -> RuntimeIdentity:
    """Read and fully validate one runtime identity, failing closed."""
    if not isinstance(path, Path):
        raise RuntimeIdentityError("identity path must be a pathlib.Path")
    if not path.is_file():
        raise RuntimeIdentityError(
            "runtime identity is absent: this data root is not a committed "
            "deployment"
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise RuntimeIdentityError(
            f"runtime identity could not be read: {exc}"
        ) from exc
    try:
        decoded = strict_json_loads(raw_bytes)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeIdentityError(
            f"runtime identity is not strict JSON: {exc}"
        ) from exc
    return validated_identity_mapping(decoded)


def _make_directory_durable(directory: Path) -> bool:
    """Attempt D-6 namespace durability, reporting whether it was achieved."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def publish_identity(path: object, identity: RuntimeIdentity) -> bool:
    """Publish the commit marker under D-1 through D-6, returning D-6 status.

    The return value reports whether namespace durability was achieved on this
    platform. It is never swallowed: callers surface it, because D70 section 5
    accepts unavailability as residual R-1 only when it is visible.
    """
    if not isinstance(path, Path):
        raise RuntimeIdentityError("identity path must be a pathlib.Path")
    if type(identity) is not RuntimeIdentity:
        raise RuntimeIdentityError("identity must be a RuntimeIdentity")
    if path.exists():
        raise RuntimeIdentityError(
            "runtime identity already exists: publication never overwrites"
        )

    body = canonical_json_bytes(identity_mapping(identity))
    directory = path.parent
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".runtime-identity.",
        suffix=".tmp",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise RuntimeIdentityError(
                "runtime identity already exists: publication never overwrites"
            ) from exc
    except OSError as exc:
        raise RuntimeIdentityError(
            f"runtime identity could not be published: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass

    namespace_durable = _make_directory_durable(directory)

    published = read_identity(path)
    if published != identity:
        raise RuntimeIdentityError(
            "published runtime identity does not read back exactly"
        )
    return namespace_durable
