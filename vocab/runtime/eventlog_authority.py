"""The sole deployment journal authority, frozen by D70 section 7.

This module's entire abstract syntax tree is restricted by a positive
structural allowlist, and the body of its one function is frozen to an exact
shape: guard clauses, one acquisition, one strict read of that exact object,
one return of that exact object. No production journal object can leave this
module without having been strictly read.

Acquisition uses EventLog.open_existing rather than the constructor. The
constructor opens in append mode and therefore creates a missing file, so the
D69 P1a zero-production-constructor invariant remains in force here too: this
module never calls EventLog(...). open_existing opens without O_CREAT, so a
journal that disappears before acquisition fails loudly instead of being
recreated empty, and an empty history reads clean.

Do not add a helper, a constant, a class, a second assignment, an alias, or an
import. The function carries no return annotation, because an annotation naming
the journal class would place a second occurrence of that name outside the one
approved acquisition expression.

Callers must hold the deployment lock.
"""

from __future__ import annotations

from pathlib import Path

from ..events import EventLog, EventLogCorruptionError, UnsupportedEventVersionError
from .errors import RuntimeEventLogError


def open_runtime_event_log(path: Path):
    """Acquire, strictly read, and return one existing deployment journal."""
    if not isinstance(path, Path):
        raise RuntimeEventLogError("deployment journal path must be a pathlib.Path")
    if not path.is_absolute():
        raise RuntimeEventLogError("deployment journal path must be absolute")
    try:
        journal = EventLog.open_existing(path)
        journal.read_strict()
    except (
        EventLogCorruptionError,
        UnsupportedEventVersionError,
        OSError,
    ) as exc:
        raise RuntimeEventLogError(
            f"deployment journal could not be acquired and read: {exc}"
        ) from exc
    return journal
