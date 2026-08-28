"""Seam-specific normalization of operational failures (D70 section 18).

Normalization is deliberately per seam. There is no global taxonomy, and in
particular ValueError is never treated as operational across arbitrary blocks:
several core errors happen to be ValueError subclasses, but that does not make
every ValueError an operational refusal, and a defect that raises a bare
ValueError must surface rather than becoming exit 1.

Each context manager below wraps one narrow operation and catches only the
exception family that operation can legitimately raise. Anything else, and
every programming defect, is allowed to escape with its traceback.

The deployment journal's acquisition and strict-read seam is not here. It lives inside
vocab/runtime/eventlog_authority.py, wrapped around the exact acquisition and read_strict calls,
because that module is the only one permitted to import the journal class and
its structural shape is frozen by the D70 section 7 invariant.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..anki import AnkiConnectError
from ..artifact_store import ArtifactStoreError
from ..capture_ledger import CaptureLedgerError
from ..corpus import CorpusScanError
from ..exposure import ExposureLedgerError
from ..transcription_ledger import TranscriptionLedgerError
from .errors import VocabRuntimeError


ANKI_SEAM: tuple[type[BaseException], ...] = (AnkiConnectError,)
CORPUS_SEAM: tuple[type[BaseException], ...] = (CorpusScanError, AnkiConnectError)
ARTIFACT_SEAM: tuple[type[BaseException], ...] = (ArtifactStoreError,)
LEDGER_SEAM: tuple[type[BaseException], ...] = (
    CaptureLedgerError,
    ExposureLedgerError,
    TranscriptionLedgerError,
)
FILESYSTEM_SEAM: tuple[type[BaseException], ...] = (OSError,)


@contextmanager
def normalized(
    error_type: type[VocabRuntimeError],
    message: str,
    *,
    catching: tuple[type[BaseException], ...],
) -> Iterator[None]:
    """Wrap one narrow seam, converting only its own failure family."""
    try:
        yield
    except VocabRuntimeError:
        raise
    except catching as exc:
        raise error_type(f"{message}: {type(exc).__name__}: {exc}") from exc
