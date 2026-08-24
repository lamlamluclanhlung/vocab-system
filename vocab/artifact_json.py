"""Pure canonical JSON and strict JSON transport infrastructure."""

from __future__ import annotations

import hashlib
import json


class ArtifactJSONError(ValueError):
    """Raised when artifact bytes are not strict standards-compliant JSON."""


def canonical_json_bytes(value: object) -> bytes:
    """Return the frozen canonical UTF-8 JSON representation of a value."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return the full lowercase SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def strict_json_loads(raw: bytes) -> object:
    """Load strict UTF-8 JSON with duplicate keys and constants rejected."""
    if not isinstance(raw, bytes):
        raise TypeError("artifact body must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactJSONError("artifact must be valid UTF-8") from None

    def reject_constant(value: str) -> object:
        raise ArtifactJSONError(f"non-standard JSON constant: {value}")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactJSONError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=exact_object,
        )
    except ArtifactJSONError:
        raise
    except json.JSONDecodeError:
        raise ArtifactJSONError("artifact must be valid JSON") from None
