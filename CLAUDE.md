# CLAUDE.md

## Repository agent rules

Read and obey `AGENTS.md` before making any repository change.

`AGENTS.md` remains the authoritative source for repository-wide agent rules.
This file adds Claude Code workflow constraints and must not override `AGENTS.md`.

## Source precedence

Use project sources in exactly this order:

1. `vocab/contracts.py` and `vocab/models.py`
2. `DECISIONS.md`
3. `docs/VOCAB_SYSTEM_SPEC.md`
4. Files explicitly named in the task

`docs/VOCAB_SYSTEM_SPEC.md` is historical and non-normative when it conflicts
with sources 1 or 2.

If sources conflict or the assigned task is ambiguous, stop and report the
conflict or ambiguity. Do not guess a resolution and do not modify a frozen or
human-owned file to resolve it.

## Before implementation

Before writing or modifying repository files, report:

1. current branch
2. current HEAD
3. git status
4. existing untracked files

Preserve all pre-existing untracked files. Do not stage, delete, overwrite,
rename, or otherwise modify them unless the human explicitly assigns that task.

## Frozen and human-owned files

Never modify a human-owned or frozen file unless the human explicitly
authorizes modification of that specific file for the current task.

Follow the human-owned file rules in `AGENTS.md`.

## Git safety

Do not:

- rebase
- amend commits
- force-push
- use `git reset --hard`
- rewrite published history
- discard unrelated local work

Do not commit unless explicitly authorized for the current task.

Do not push unless explicitly authorized for the current task and destination.

## Fail-closed behavior

Do not:

- silently repair invalid data
- silently normalize ambiguous input
- invent missing identifiers or provenance
- add fallback values to bypass validation
- weaken validators to make implementation pass
- modify, weaken, skip, or delete an existing test merely to make code pass

If a frozen contract, decision, implementation, and test disagree, stop and
report the contradiction.

## Tests

Actually execute every test that you report as having passed.

Never report a test, test group, regression suite, compile check, or other
validation command as successful unless you personally executed that command
in the current work.

Run the tests required by the assigned task. Run broader regression suites when
the task explicitly requires them or when the implementation scope reasonably
requires regression validation.

Report separately:

- commands actually executed
- passed results
- failed results
- tests not executed

## Checkpoint scope

Do not infer, invent, rename, merge, split, or extend roadmap checkpoints.

A checkpoint name and scope exist only when:

- explicitly supplied by the human for the current task; or
- explicitly established by an applicable normative repository source.

Historical or non-normative documentation may provide context, but it does not
by itself establish a new checkpoint scope when the normative sources do not.

Work only within the explicitly assigned checkpoint.

Do not begin a later checkpoint without explicit instruction.

## Task completion

After implementation, report:

1. files changed
2. tests and validation commands actually executed
3. results
4. assumptions made
5. unresolved ambiguities or risks
6. current git status

Do not commit or push unless separately authorized.
