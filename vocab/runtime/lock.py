"""Ephemeral O_EXCL deployment lock frozen by D70 section 12."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..artifact_json import ArtifactJSONError, canonical_json_bytes, strict_json_loads
from .errors import RuntimeLockError


@dataclass(frozen=True, slots=True)
class LockState:
    """The observable state of one deployment lock path."""

    held: bool
    pid: int | None = None
    acquired_utc: str | None = None
    readable: bool = True


def read_lock_state(path: object) -> LockState:
    """Observe lock state without creating, modifying, or removing anything."""
    if not isinstance(path, Path):
        raise RuntimeLockError("lock path must be a pathlib.Path")
    if not path.exists():
        return LockState(held=False)
    try:
        decoded = strict_json_loads(path.read_bytes())
    except (OSError, ArtifactJSONError, TypeError):
        return LockState(held=True, readable=False)
    if not isinstance(decoded, dict):
        return LockState(held=True, readable=False)
    pid = decoded.get("pid")
    acquired = decoded.get("acquired_utc")
    if type(pid) is not int or type(acquired) is not str:
        return LockState(held=True, readable=False)
    return LockState(held=True, pid=pid, acquired_utc=acquired)


def describe_lock_holder(path: Path) -> str:
    """Render the recorded holder of a held lock for a refusal message."""
    state = read_lock_state(path)
    if not state.held:
        return "not held"
    if not state.readable:
        return "held by an unreadable lock record"
    return f"held by pid {state.pid} since {state.acquired_utc}"


class DeploymentLock:
    """One advisory O_EXCL lock. Never broken, expired, or forced."""

    def __init__(self, path: object) -> None:
        if not isinstance(path, Path):
            raise RuntimeLockError("lock path must be a pathlib.Path")
        self.path = path
        self._acquired = False

    def acquire(self) -> None:
        """Acquire the lock, failing closed when it is held or stale."""
        if self._acquired:
            raise RuntimeLockError("deployment lock is already acquired")
        body = canonical_json_bytes(
            {
                "pid": os.getpid(),
                "acquired_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        try:
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError as exc:
            raise RuntimeLockError(
                f"deployment lock is {describe_lock_holder(self.path)}; "
                "clearing a stale lock is a human act"
            ) from exc
        except OSError as exc:
            raise RuntimeLockError(
                f"deployment lock could not be acquired: {exc}"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            self._remove()
            raise RuntimeLockError(
                f"deployment lock could not be recorded: {exc}"
            ) from exc
        self._acquired = True

    def release(self) -> None:
        """Release the lock. Only the acquiring object may remove it."""
        if not self._acquired:
            return
        self._acquired = False
        self._remove()

    def _remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> DeploymentLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
