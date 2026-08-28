"""Frozen durable layout of one D70 deployment data root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import RuntimeLayoutError


DURABLE_LAYOUT_VERSION = 1

IDENTITY_FILE_NAME = "runtime-identity.json"
EVENT_LOG_FILE_NAME = "events.jsonl"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SESSION_DIRECTORY_NAME = "sessions"
EXPOSURE_LEDGER_FILE_NAME = "t12-exposures.jsonl"
CAPTURE_LEDGER_FILE_NAME = "t12-captures.jsonl"
DISPOSITION_LEDGER_FILE_NAME = "t12-dispositions.jsonl"
TRANSCRIPTION_LEDGER_FILE_NAME = "t12-transcriptions.jsonl"

# D70 section 3: runtime.lock is an ephemeral coordination path, never a
# durable layout entry, and is excluded from every completeness check.
LOCK_FILE_NAME = "runtime.lock"

DURABLE_LAYOUT_FILE_NAMES: tuple[str, ...] = (
    IDENTITY_FILE_NAME,
    EVENT_LOG_FILE_NAME,
    EXPOSURE_LEDGER_FILE_NAME,
    CAPTURE_LEDGER_FILE_NAME,
    DISPOSITION_LEDGER_FILE_NAME,
    TRANSCRIPTION_LEDGER_FILE_NAME,
)

DURABLE_LAYOUT_DIRECTORY_NAMES: tuple[str, ...] = (
    ARTIFACT_DIRECTORY_NAME,
    SESSION_DIRECTORY_NAME,
)

DURABLE_LAYOUT_ENTRY_NAMES: tuple[str, ...] = (
    *DURABLE_LAYOUT_FILE_NAMES,
    *DURABLE_LAYOUT_DIRECTORY_NAMES,
)


@dataclass(frozen=True, slots=True)
class DeploymentLayout:
    """Every derived path of one deployment, keyed only by its data root."""

    data_root: Path

    @property
    def identity_path(self) -> Path:
        return self.data_root / IDENTITY_FILE_NAME

    @property
    def event_log_path(self) -> Path:
        return self.data_root / EVENT_LOG_FILE_NAME

    @property
    def artifact_root(self) -> Path:
        return self.data_root / ARTIFACT_DIRECTORY_NAME

    @property
    def session_root(self) -> Path:
        return self.data_root / SESSION_DIRECTORY_NAME

    @property
    def exposure_path(self) -> Path:
        return self.data_root / EXPOSURE_LEDGER_FILE_NAME

    @property
    def capture_path(self) -> Path:
        return self.data_root / CAPTURE_LEDGER_FILE_NAME

    @property
    def disposition_path(self) -> Path:
        return self.data_root / DISPOSITION_LEDGER_FILE_NAME

    @property
    def transcription_path(self) -> Path:
        return self.data_root / TRANSCRIPTION_LEDGER_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.data_root / LOCK_FILE_NAME


def build_layout(data_root: object) -> DeploymentLayout:
    """Derive the frozen layout of one absolute deployment data root."""
    if not isinstance(data_root, Path):
        raise RuntimeLayoutError("data root must be a pathlib.Path")
    if not data_root.is_absolute():
        raise RuntimeLayoutError("data root must be an absolute path")
    return DeploymentLayout(data_root=data_root)


def missing_durable_entries(layout: DeploymentLayout) -> tuple[str, ...]:
    """Return each durable entry that is absent or is the wrong kind."""
    missing: list[str] = []
    for name in DURABLE_LAYOUT_FILE_NAMES:
        if not (layout.data_root / name).is_file():
            missing.append(name)
    for name in DURABLE_LAYOUT_DIRECTORY_NAMES:
        if not (layout.data_root / name).is_dir():
            missing.append(name)
    return tuple(missing)
