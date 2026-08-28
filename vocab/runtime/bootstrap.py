"""Two-phase deployment bootstrap frozen by D70 sections 9 and 10."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ..artifact_store import ArtifactStore
from ..corpus import RegistryEntry, read_registry_snapshot
from ..exposure import validate_t12_histories
from ..response_capture import initialize_t12_ledgers
from ..transcription_ledger import read_transcription_ledger
from .config import RuntimeConfig
from .errors import RuntimeBootstrapError
from .eventlog_authority import open_runtime_event_log
from .identity import (
    IDENTITY_VERSION,
    RuntimeIdentity,
    publish_identity,
    registry_digest,
)
from .layout import (
    DURABLE_LAYOUT_VERSION,
    LOCK_FILE_NAME,
    DeploymentLayout,
    build_layout,
    missing_durable_entries,
)
from .lock import DeploymentLock
from .normalize import (
    ARTIFACT_SEAM,
    CORPUS_SEAM,
    FILESYSTEM_SEAM,
    LEDGER_SEAM,
    normalized,
)
from .preflight import PreflightReport, run_bootstrap_preflight


class _BootstrapAnkiPort(Protocol):
    def verify_note_type(self) -> bool: ...

    def get_deck_config(self, deck_name: str) -> object: ...

    def verify_leech_config(self, deck_name: str) -> bool: ...

    def find_notes(self, query: str) -> list[int]: ...

    def notes_info(self, note_ids: object) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class BootstrapPreconditions:
    """The complete side-effect-free result of bootstrap phase 0."""

    layout: DeploymentLayout
    preflight: PreflightReport
    registry: tuple[RegistryEntry, ...]
    registry_count: int
    registry_digest: str
    confirmations_present: bool


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """The outcome of a completed bootstrap phase 1."""

    layout: DeploymentLayout
    identity: RuntimeIdentity
    namespace_durable: bool


def _require_absent_or_empty(layout: DeploymentLayout) -> None:
    root = layout.data_root
    if not root.exists():
        return
    if not root.is_dir():
        raise RuntimeBootstrapError("data root exists and is not a directory")
    # D70 s3: runtime.lock is ephemeral and is never deployment state. D70 s9
    # makes O_EXCL the race resolver, so a concurrent bootstrap must reach lock
    # acquisition and be refused there rather than here.
    if any(entry.name != LOCK_FILE_NAME for entry in root.iterdir()):
        raise RuntimeBootstrapError(
            "data root is not empty; bootstrap never adopts, resumes, or "
            "repairs existing state. Inspect and remove it by hand."
        )


def evaluate_preconditions(
    config: RuntimeConfig,
    anki: _BootstrapAnkiPort,
    *,
    confirm_new_deployment: bool,
    confirm_clean_production_profile: bool,
) -> BootstrapPreconditions:
    """Run bootstrap phase 0. Creates, modifies, and removes nothing."""
    if type(confirm_new_deployment) is not bool:
        raise RuntimeBootstrapError("confirm_new_deployment must be a bool")
    if type(confirm_clean_production_profile) is not bool:
        raise RuntimeBootstrapError(
            "confirm_clean_production_profile must be a bool"
        )

    layout = build_layout(config.data_root)
    _require_absent_or_empty(layout)

    preflight = run_bootstrap_preflight(config, anki)
    if not preflight.ok:
        raise RuntimeBootstrapError(
            "bootstrap preflight failed:\n" + preflight.render()
        )

    with normalized(
        RuntimeBootstrapError,
        "profile registry could not be read",
        catching=CORPUS_SEAM,
    ):
        registry = read_registry_snapshot(anki)
    unit_keys = [entry.unit_key for entry in registry]
    digest = registry_digest(unit_keys)

    return BootstrapPreconditions(
        layout=layout,
        preflight=preflight,
        registry=registry,
        registry_count=len(registry),
        registry_digest=digest,
        confirmations_present=(
            confirm_new_deployment and confirm_clean_production_profile
        ),
    )


def create_deployment(preconditions: BootstrapPreconditions) -> BootstrapResult:
    """Run bootstrap phase 1, publishing the commit marker last."""
    if type(preconditions) is not BootstrapPreconditions:
        raise RuntimeBootstrapError("preconditions must come from phase 0")
    if not preconditions.confirmations_present:
        raise RuntimeBootstrapError(
            "both confirmations are required before any filesystem side effect"
        )

    layout = preconditions.layout
    root = layout.data_root
    if root.exists():
        _require_absent_or_empty(layout)
    else:
        with normalized(
            RuntimeBootstrapError,
            "data root could not be created",
            catching=FILESYSTEM_SEAM,
        ):
            root.mkdir(parents=True, exist_ok=False)

    lock = DeploymentLock(layout.lock_path)
    lock.acquire()
    try:
        residue = sorted(
            entry.name
            for entry in root.iterdir()
            if entry.name != LOCK_FILE_NAME
        )
        if residue:
            raise RuntimeBootstrapError(
                f"data root gained content during bootstrap: {residue}"
            )

        with normalized(
            RuntimeBootstrapError,
            "durable directories could not be created",
            catching=FILESYSTEM_SEAM,
        ):
            layout.artifact_root.mkdir(parents=False, exist_ok=False)
            layout.session_root.mkdir(parents=False, exist_ok=False)

        try:
            handle = layout.event_log_path.open("xb")
        except FileExistsError as exc:
            raise RuntimeBootstrapError(
                "deployment journal already exists; bootstrap never adopts one"
            ) from exc
        handle.close()

        with normalized(
            RuntimeBootstrapError,
            "T12 ledgers could not be created",
            catching=LEDGER_SEAM + ARTIFACT_SEAM + FILESYSTEM_SEAM,
        ):
            store = ArtifactStore(layout.artifact_root)
            initialize_t12_ledgers(
                exposure_path=layout.exposure_path,
                capture_path=layout.capture_path,
                disposition_path=layout.disposition_path,
                artifact_store=store,
                no_historical_t12_state=True,
            )

        try:
            handle = layout.transcription_path.open("xb")
        except FileExistsError as exc:
            raise RuntimeBootstrapError(
                "transcription ledger already exists"
            ) from exc
        handle.close()

        with normalized(
            RuntimeBootstrapError,
            "created deployment state did not validate",
            catching=LEDGER_SEAM + ARTIFACT_SEAM,
        ):
            _validate_created_state(layout)

        identity = RuntimeIdentity(
            identity_version=IDENTITY_VERSION,
            runtime_id=str(uuid.uuid4()),
            layout_version=DURABLE_LAYOUT_VERSION,
            created_utc=datetime.now(timezone.utc).isoformat(),
            bootstrap_registry_count=preconditions.registry_count,
            bootstrap_registry_digest=preconditions.registry_digest,
        )
        namespace_durable = publish_identity(layout.identity_path, identity)
    finally:
        lock.release()

    missing = missing_durable_entries(layout)
    if missing:
        raise RuntimeBootstrapError(
            f"bootstrap finished with absent durable entries: {list(missing)}"
        )
    return BootstrapResult(
        layout=layout,
        identity=identity,
        namespace_durable=namespace_durable,
    )


def _validate_created_state(layout: DeploymentLayout) -> None:
    """Re-read every durable artifact created so far, before committing."""
    journal = open_runtime_event_log(layout.event_log_path)
    history = journal.read_strict()
    if history:
        raise RuntimeBootstrapError(
            "a freshly created deployment journal must be empty"
        )
    validate_t12_histories(
        exposure_path=layout.exposure_path,
        capture_path=layout.capture_path,
        disposition_path=layout.disposition_path,
        artifact_store=ArtifactStore(layout.artifact_root),
    )
    if read_transcription_ledger(layout.transcription_path):
        raise RuntimeBootstrapError(
            "a freshly created transcription ledger must be empty"
        )
