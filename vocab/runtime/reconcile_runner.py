"""T9 reconcile runner, frozen by D70 section 16.

Targets are discovered globally before the first lifecycle write, one
timezone-aware instant is captured for the whole run, and Units are processed in
unit_key order. A known operational failure in one Unit produces one ERROR line
and the run continues; anything outside the T9 taxonomy surfaces as a defect.

Reactivation and leech rescue are reported, never performed here: reconcile_unit
already owns whatever lifecycle mutation is legitimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TextIO

from ..models import ReconcileRunResult
from ..reconcile import reconcile_unit
from .errors import RuntimeReconcileError
from .normalize import RECONCILE_SEAM, normalized
from .targets import ReconcileTarget


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """One Unit's outcome, either a T9 result or a known operational failure."""

    unit_key: str
    note_id: int
    result: ReconcileRunResult | None = None
    error: str = ""

    @property
    def failed(self) -> bool:
        return self.result is None


def _render(outcome: ReconcileOutcome) -> str:
    if outcome.failed:
        return f"ERROR  {outcome.unit_key}  note={outcome.note_id}  {outcome.error}"

    result = outcome.result
    assert result is not None
    parts = [f"OK     {outcome.unit_key}  note={outcome.note_id}"]
    for label, values in (
        ("committed", result.committed_transition_ids),
        ("recovered", result.recovered_transition_ids),
        ("aborted", result.aborted_transition_ids),
    ):
        if values:
            parts.append(f"{label}={len(values)}")
    if result.reactivation_required_card_ids:
        parts.append(
            "reactivation_required="
            + ",".join(str(card) for card in result.reactivation_required_card_ids)
        )
    if result.leech_rescue_channels:
        parts.append("leech_rescue=" + ",".join(result.leech_rescue_channels))
    return "  ".join(parts)


def run_reconcile(
    targets: tuple[ReconcileTarget, ...],
    *,
    anki: object,
    event_log: object,
    stream_out: TextIO,
) -> int:
    """Reconcile every target, returning the number that failed."""
    now = datetime.now(timezone.utc)
    failed = 0

    for target in targets:
        try:
            with normalized(
                RuntimeReconcileError,
                f"reconcile failed for {target.unit_key}",
                catching=RECONCILE_SEAM,
            ):
                result = reconcile_unit(
                    target.note_id,
                    anki=anki,
                    event_log=event_log,
                    now=now,
                )
        except RuntimeReconcileError as exc:
            failed += 1
            outcome = ReconcileOutcome(
                unit_key=target.unit_key,
                note_id=target.note_id,
                error=str(exc),
            )
        else:
            outcome = ReconcileOutcome(
                unit_key=target.unit_key,
                note_id=target.note_id,
                result=result,
            )
        stream_out.write(_render(outcome) + "\n")

    stream_out.write(f"\ntotal={len(targets)}  failed={failed}\n")
    if failed == 0:
        stream_out.write("reconcile OK\n")
    return failed
