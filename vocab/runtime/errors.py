"""Fail-closed error types for the D70 operational runtime."""

from __future__ import annotations


class VocabRuntimeError(Exception):
    """Base class for every fail-closed operational runtime refusal."""


class RuntimeConfigError(VocabRuntimeError):
    """Raised when a configuration violates the closed D70 section 6 schema."""


class RuntimeIdentityError(VocabRuntimeError):
    """Raised when a runtime identity is absent, invalid, or unpublishable."""


class RuntimeLayoutError(VocabRuntimeError):
    """Raised when the durable deployment layout is absent or malformed."""


class RuntimeLockError(VocabRuntimeError):
    """Raised when the deployment lock is held, stale, or unusable."""


class RuntimeEventLogError(VocabRuntimeError):
    """Raised when a deployment journal path cannot be trusted."""


class RuntimeBootstrapError(VocabRuntimeError):
    """Raised when a deployment cannot be created under D70 section 9."""


class RuntimePreflightError(VocabRuntimeError):
    """Raised when a preflight obligation fails."""
