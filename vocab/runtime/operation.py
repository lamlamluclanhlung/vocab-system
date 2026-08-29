"""The D70 section 11 normal write protocol, as one reusable composition.

Every write-capable command runs exactly this order: validate the committed
deployment, acquire the lock, run the full write-preflight while holding it,
and only then perform the operation. The helper deliberately catches nothing,
so each command keeps its own narrow failure taxonomy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .config import RuntimeConfig
from .errors import RuntimePreflightError
from .identity import read_identity
from .layout import DeploymentLayout, build_layout
from .lock import DeploymentLock
from .preflight import run_runtime_write_preflight


@contextmanager
def write_operation(
    config: RuntimeConfig,
    anki: object,
) -> Iterator[DeploymentLayout]:
    """Yield the layout of an operationally valid, locked deployment."""
    layout = build_layout(config.data_root)
    read_identity(layout.identity_path)

    lock = DeploymentLock(layout.lock_path)
    lock.acquire()
    try:
        report = run_runtime_write_preflight(config, layout, anki)
        if not report.ok:
            raise RuntimePreflightError(
                "runtime write-preflight failed:\n" + report.render()
            )
        yield layout
    finally:
        lock.release()
