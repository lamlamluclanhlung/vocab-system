"""The three distinct preflights frozen by D70 section 13."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..artifact_store import ArtifactStore
from ..exposure import validate_t12_histories
from ..transcription_ledger import read_transcription_ledger
from .config import RuntimeConfig
from .errors import RuntimePreflightError, VocabRuntimeError
from .normalize import ANKI_SEAM, ARTIFACT_SEAM, LEDGER_SEAM, normalized
from .eventlog_authority import open_runtime_event_log
from .identity import read_identity
from .layout import DeploymentLayout, missing_durable_entries
from .lock import read_lock_state


PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUATED = "NOT EVALUATED"


class _AnkiPort(Protocol):
    def verify_note_type(self) -> bool: ...

    def get_deck_config(self, deck_name: str) -> object: ...

    def verify_leech_config(self, deck_name: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One named preflight obligation and its outcome."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """The complete outcome of one preflight run."""

    checks: tuple[PreflightCheck, ...]

    @property
    def failed(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.status == FAIL)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        width = max(len(check.name) for check in self.checks)
        lines = []
        for check in self.checks:
            suffix = f"  {check.detail}" if check.detail else ""
            lines.append(f"  {check.name.ljust(width)}  {check.status}{suffix}")
        return "\n".join(lines)


def _check(name: str, action) -> PreflightCheck:
    try:
        detail = action()
    except VocabRuntimeError as exc:
        return PreflightCheck(
            name=name, status=FAIL, detail=f"{type(exc).__name__}: {exc}"
        )
    return PreflightCheck(name=name, status=PASS, detail=detail or "")


def _anki_checks(config: RuntimeConfig, anki: _AnkiPort) -> list[PreflightCheck]:
    deck_name = config.anki.deck_name

    def note_type() -> str:
        with normalized(
            RuntimePreflightError, "note type", catching=ANKI_SEAM
        ):
            anki.verify_note_type()
        return ""

    def deck() -> str:
        with normalized(RuntimePreflightError, "deck", catching=ANKI_SEAM):
            anki.get_deck_config(deck_name)
        return deck_name

    def leech() -> str:
        with normalized(
            RuntimePreflightError, "leech config", catching=ANKI_SEAM
        ):
            anki.verify_leech_config(deck_name)
        return ""

    return [
        _check("anki.note_type", note_type),
        _check("anki.deck", deck),
        _check("anki.leech_config", leech),
    ]


def _existing_artifact_store(layout: DeploymentLayout) -> ArtifactStore:
    """Build a store only for an artifact root that already exists.

    ArtifactStore.__init__ calls mkdir(parents=True, exist_ok=True), so
    constructing one unconditionally would let a check silently recreate a
    missing artifacts directory. Bootstrap is the only path allowed to create
    it, so this refuses instead.
    """
    if not layout.artifact_root.is_dir():
        raise RuntimePreflightError(
            "artifact root is absent; only bootstrap may create it"
        )
    with normalized(
        RuntimePreflightError, "artifact root", catching=ARTIFACT_SEAM
    ):
        return ArtifactStore(layout.artifact_root)


def _layout_check(layout: DeploymentLayout) -> PreflightCheck:
    def layout_complete() -> str:
        missing = missing_durable_entries(layout)
        if missing:
            raise RuntimePreflightError(
                f"durable layout entries absent or wrong kind: {list(missing)}"
            )
        return "8 durable entries"

    return _check("layout.durable_entries", layout_complete)


def _identity_check(layout: DeploymentLayout) -> PreflightCheck:
    def identity() -> str:
        record = read_identity(layout.identity_path)
        return f"runtime_id={record.runtime_id}"

    return _check("identity.committed", identity)


def run_bootstrap_preflight(
    config: RuntimeConfig,
    anki: _AnkiPort,
) -> PreflightReport:
    """Preflight for deployment creation. Never reads a runtime identity."""
    return PreflightReport(checks=tuple(_anki_checks(config, anki)))


def run_runtime_write_preflight(
    config: RuntimeConfig,
    layout: DeploymentLayout,
    anki: _AnkiPort,
) -> PreflightReport:
    """Full preflight, run only while the deployment lock is held."""
    checks: list[PreflightCheck] = [
        _identity_check(layout),
        _layout_check(layout),
        *_anki_checks(config, anki),
    ]

    def history() -> str:
        journal = open_runtime_event_log(layout.event_log_path)
        return f"{len(journal.read_strict())} events"

    def t12() -> str:
        store = _existing_artifact_store(layout)
        with normalized(
            RuntimePreflightError,
            "T12 triple",
            catching=LEDGER_SEAM + ARTIFACT_SEAM,
        ):
            validate_t12_histories(
                exposure_path=layout.exposure_path,
                capture_path=layout.capture_path,
                disposition_path=layout.disposition_path,
                artifact_store=store,
            )
        return ""

    def transcriptions() -> str:
        with normalized(
            RuntimePreflightError, "transcription ledger", catching=LEDGER_SEAM
        ):
            records = read_transcription_ledger(layout.transcription_path)
        return f"{len(records)} records"

    checks.append(_check("eventlog.strict_history", history))
    checks.append(_check("t12.triple_consistency", t12))
    checks.append(_check("t12.transcription_ledger", transcriptions))
    return PreflightReport(checks=tuple(checks))


def run_standalone_preflight(
    config: RuntimeConfig,
    layout: DeploymentLayout,
    anki: _AnkiPort,
) -> PreflightReport:
    """Diagnostic preflight. Never locks and never constructs a journal.

    History and ledger consistency are reported NOT EVALUATED rather than PASS
    or FAIL, so that a concurrent writer is never reported as corruption, and
    so that a concurrently removed journal is never recreated here.
    """
    checks: list[PreflightCheck] = [
        _identity_check(layout),
        _layout_check(layout),
        *_anki_checks(config, anki),
    ]
    state = read_lock_state(layout.lock_path)
    if not state.held:
        lock_detail = "FREE"
    elif state.readable:
        lock_detail = f"HELD pid={state.pid} since={state.acquired_utc}"
    else:
        lock_detail = "HELD (unreadable lock record)"
    checks.append(PreflightCheck(name="lock.state", status=PASS, detail=lock_detail))
    checks.append(
        PreflightCheck(
            name="eventlog.strict_history",
            status=NOT_EVALUATED,
            detail="requires the deployment lock",
        )
    )
    checks.append(
        PreflightCheck(
            name="t12.triple_consistency",
            status=NOT_EVALUATED,
            detail="requires the deployment lock",
        )
    )
    return PreflightReport(checks=tuple(checks))
