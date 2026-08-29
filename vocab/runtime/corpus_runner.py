"""T10 corpus scan runner.

One global scan and emission over the existing pipeline: registry snapshot,
corpus snapshot, count, emit. T10 already owns ENCOUNTER identity and
idempotency, so a rerun over the same source, month, and corpus snapshot
appends nothing and still succeeds.

This is one operation rather than a set of independent per-item outcomes, so a
refusal is a fail-closed refusal, not a per-item failure count.
"""

from __future__ import annotations

from typing import TextIO

from ..corpus import (
    CorpusEmitReport,
    count_scan,
    emit_scan,
    read_corpus_snapshot,
)
from .config import RuntimeConfig
from .errors import RuntimeCorpusError
from .normalize import CORPUS_SEAM_SCAN, FILESYSTEM_SEAM, normalized
from .targets import read_registry


def run_corpus_scan(
    config: RuntimeConfig,
    *,
    source: str,
    month: str,
    anki: object,
    event_log: object,
    stream_out: TextIO,
) -> CorpusEmitReport:
    """Count one corpus month against the registry and emit its encounters."""
    registry = read_registry(anki)

    with normalized(
        RuntimeCorpusError,
        f"corpus snapshot could not be read from {config.corpus_root}",
        catching=CORPUS_SEAM_SCAN + FILESYSTEM_SEAM,
    ):
        corpus = read_corpus_snapshot(
            config.corpus_root,
            source=source,
            month=month,
        )

    with normalized(
        RuntimeCorpusError,
        "corpus scan could not be counted",
        catching=CORPUS_SEAM_SCAN,
    ):
        result = count_scan(registry, corpus)

    stream_out.write(f"source                 {result.source}\n")
    stream_out.write(f"month                  {result.month}\n")
    stream_out.write(f"corpus_snapshot_digest {result.corpus_snapshot_digest}\n")
    stream_out.write(f"corpus_file_count      {result.corpus_file_count}\n")
    stream_out.write(f"registry_units         {len(registry)}\n")
    stream_out.write(f"counted_units          {len(result.counts)}\n")

    with normalized(
        RuntimeCorpusError,
        "corpus encounters could not be emitted",
        catching=CORPUS_SEAM_SCAN,
    ):
        report = emit_scan(result, event_log=event_log)

    stream_out.write(f"appended               {len(report.appended_encounter_ids)}\n")
    stream_out.write(f"existing               {len(report.existing_encounter_ids)}\n")
    stream_out.write("corpus scan OK\n")
    return report
