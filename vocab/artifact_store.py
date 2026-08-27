"""Immutable content-addressed storage for exact T12 artifact bytes."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from .contracts import ASSESSMENT_ARTIFACT_REF_PATTERN


_ARTIFACT_REF_RE = re.compile(ASSESSMENT_ARTIFACT_REF_PATTERN)


class ArtifactStoreError(ValueError):
    """Raised when immutable artifact bytes cannot be trusted."""


class ArtifactStore:
    """Map exact bytes to verified SHA-256 refs at an explicit root path."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if root is None:
            raise TypeError("artifact root must be explicit")
        self.root = Path(root)
        if not self.root.name:
            raise ValueError("artifact root must identify a directory")
        if self.root.exists() and not self.root.is_dir():
            raise ArtifactStoreError("artifact root is not a directory")
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        """Durably publish exact bytes and return their verified content ref."""
        if type(data) is not bytes:
            raise TypeError("artifact data must be exact bytes")
        digest = hashlib.sha256(data).hexdigest()
        ref = f"sha256:{digest}"
        path = self._path_for_ref(ref)

        if path.exists():
            self._verify_path(path, ref, expected=data)
            return ref

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=self.root,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                file_descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                pass
            self._verify_path(path, ref, expected=data)
        except OSError as exc:
            raise ArtifactStoreError(
                f"artifact could not be durably published: {exc}"
            ) from exc
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        return ref

    def read(self, ref: object) -> bytes:
        """Return exact artifact bytes after ref and digest verification."""
        validated_ref = self._validated_ref(ref)
        path = self._path_for_ref(validated_ref)
        return self._verify_path(path, validated_ref)

    def _path_for_ref(self, ref: str) -> Path:
        return self.root / ref.removeprefix("sha256:")

    @staticmethod
    def _validated_ref(ref: object) -> str:
        if type(ref) is not str or _ARTIFACT_REF_RE.fullmatch(ref) is None:
            raise ArtifactStoreError("artifact ref is invalid")
        return ref

    @staticmethod
    def _verify_path(
        path: Path,
        ref: str,
        *,
        expected: bytes | None = None,
    ) -> bytes:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            raise ArtifactStoreError("referenced artifact is missing or unreadable") from exc
        actual_ref = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual_ref != ref:
            raise ArtifactStoreError("stored artifact bytes do not match their ref")
        if expected is not None and data != expected:
            raise ArtifactStoreError("stored artifact bytes conflict with new bytes")
        return data
