# Operational runbook

Normative authority is `DECISIONS.md`. This runbook is operational guidance.

## Before the first production write

The Anki profile is the registry boundary. Before bootstrap, either use a clean
production profile and move all testing to a separate profile, or back up and
explicitly remove smoke and test notes yourself. Nothing is ever deleted
automatically. Bootstrap displays the full registry and requires
`--confirm-clean-production-profile`; that confirmation is a human attestation
and is deliberately not machine-verifiable.

## Deployment states

| State | Meaning |
|-------|---------|
| Incomplete data root | `runtime-identity.json` absent or invalid. Not a deployment. |
| Committed deployment | Identity present and valid. Says nothing about the rest of the layout. |
| Operationally valid | Committed, and the full write-preflight passed while the lock was held. |

Only an operationally valid deployment may be written to.

## Layout

Eight durable entries under `data_root`, none configurable:

```
runtime-identity.json  events.jsonl  artifacts/  sessions/
t12-exposures.jsonl  t12-captures.jsonl
t12-dispositions.jsonl  t12-transcriptions.jsonl
```

`runtime.lock` is ephemeral coordination, never deployment state. Absent means
unlocked; present means locked.

## Durability of the commit marker

Identity publication writes a same-directory temporary file, fsyncs its bytes,
publishes atomically with `os.link` (which refuses to overwrite), attempts
namespace durability by fsyncing the containing directory, then reads the file
back and validates it in full.

Namespace durability is unavailable on platforms where a directory cannot be
opened for fsync, notably Windows. Bootstrap prints whether it was achieved.
This residual is bounded: losing the directory entry yields a **missing**
identity file, which is an incomplete data root and already fails closed. A
partially written identity file can never appear, because publication is atomic
and never overwrites.

## Interrupted bootstrap

A partial bootstrap is never resumed, repaired, adopted, or deleted. If a crash
happens before the identity file is published, the data root is incomplete.
Inspect it and remove it by hand, then bootstrap again.

An empty directory is the one exception: it holds no durable content, so
bootstrap may retry there without your intervention. This does **not** extend to
a data root holding a leftover `runtime.lock`. A stale lock keeps its authority,
so bootstrap refuses with exit code 3 and never breaks it; remove the lock
yourself first. See "Stale lock" below.

## Stale lock

A lock surviving a crash is stale. Every write-capable command refuses with exit
code 3 and prints the recorded pid and timestamp. There is no force flag, no
timeout expiry, and no automatic breaking. Confirm no other process is running,
then delete `runtime.lock` yourself.

## Relocating a deployment

The identity file records no absolute path, so copying a complete `data_root`
elsewhere and updating `data_root` in the configuration is legal. Restoring an
older backup over a newer history is **not** detected; that is backup
discipline, not a runtime property.

## Backup

Copy the whole `data_root`. Anki's collection is backed up separately by Anki.

## Preflight semantics

`vocab preflight` is diagnostic only. It never locks and never constructs a
journal, so it reports history and ledger consistency as `NOT EVALUATED` rather
than `PASS` or `FAIL`. It also refuses rather than repairs: because
`ArtifactStore.__init__` creates its root directory, no preflight builds one
until `artifacts/` is verified to exist. Bootstrap is the only path allowed to
create it. This keeps "another process is writing" distinct from
"the data is corrupt", and prevents a concurrently removed `events.jsonl` from
being recreated empty. Only the write-preflight taken under the lock is
authority to write.

## Release gate

```
.\.venv\Scripts\python.exe -m pytest      # no arguments; must certify D69
python -m compileall vocab tests conftest.py
git diff --check
```

Any argument to pytest, including `-k` or a path, prints
`D69 acceptance not certified` instead of certifying.
