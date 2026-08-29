# vocab-system

A personal English vocabulary system built on Anki, with evidence-disciplined
lifecycle, provenance, and crash semantics.

Anki is the source of truth for vocabulary notes. This project never mutates
note types, card templates, styling, or deck configuration, and never deletes
notes. Dormancy suspends cards and preserves the note and its revlog.

## Status

Core layers T6 through T12.4 are complete and gated. The operational runtime
(decision D70) is at Wave B: a deployment can be created and diagnosed, Units
can be forged through a human-mediated bridge, lifecycle can be reconciled, and
corpus months can be scanned. Assessment session orchestration is Wave C. T8
keeps its own CLI for now.

## Requirements

- Python 3.12
- Anki Desktop with AnkiConnect, using a **dedicated production profile**
- For audio hydration only: `pip install -r requirements-tts.txt`

## Registry isolation

The Anki **profile** is the registry boundary, not the deck. FORGE deduplicates
on `unit_key` without a deck filter, and both the T8 context exporter and the
T10 registry snapshot read the whole profile. A deck named `Vocab Lab` is not a
sandbox. Keep testing in a separate Anki profile.

## Quick start

Write a configuration file. The schema is closed: unknown keys, missing keys,
and relative paths all fail. There is no default path, no environment variable,
and no working-directory discovery.

```json
{
  "config_version": 1,
  "data_root": "D:/vocab-data/prod",
  "corpus_root": "D:/vocab-data/prod/corpus",
  "anki": {
    "endpoint": "http://127.0.0.1:8765",
    "timeout": 10.0,
    "deck_name": "Vocabulary"
  }
}
```

Create the deployment once. Bootstrap first shows every `VocabularyUnit` note in
the profile and creates nothing, so you can confirm the registry is clean:

```
python -m vocab.cli bootstrap --config D:/vocab-data/runtime.json
```

Re-run with both confirmations to proceed:

```
python -m vocab.cli bootstrap --config D:/vocab-data/runtime.json \
    --confirm-new-deployment --confirm-clean-production-profile
```

Diagnose at any time. This never locks and never writes:

```
python -m vocab.cli preflight --config D:/vocab-data/runtime.json
```

Daily commands:

```
python -m vocab.cli forge-export --source-ref corpus:bbc:2026-08-01 \
    --source-sentence "..." --out request.json
python -m vocab.cli forge-import --config runtime.json \
    --request request.json --response response.json --actor-id lam
python -m vocab.cli reconcile --config runtime.json --all
python -m vocab.cli corpus-scan --config runtime.json --source bbc --month 2026-08
```

No model is ever called for you. `forge-export` writes an artifact you take to a
model yourself, and `forge-import` replays the reply you saved.

T8 keeps its own CLI:

```
python -m vocab.t8_cli export-contexts | import-contexts | hydrate-audio
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | success |
| 1 | fail-closed refusal |
| 2 | argument usage error |
| 3 | deployment lock contention |
| 4 | per-item failures (Wave B) |

A traceback means a defect in this software, not a refusal. Only the runtime
exception taxonomy maps to exit 1; anything outside it is allowed to surface.

See `docs/RUNBOOK.md` for daily operation and recovery.
