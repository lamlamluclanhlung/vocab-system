# Vocab System — Codex Instructions

Before making any change, read the relevant project sources in this precedence order:

1. `vocab/contracts.py` and `vocab/models.py` — normative, machine-checkable contracts/data structures
2. `DECISIONS.md` — normative architectural decisions and rationale
3. `docs/VOCAB_SYSTEM_SPEC.md` — historical design narrative, build order, and lane guidance; non-normative when it conflicts with 1 or 2
4. the files explicitly named in the task

If these sources conflict, STOP and report the conflict. Do not resolve it by modifying a frozen/human-owned file.

## Human-owned files

The following files are human-owned and must be treated as read-only unless the human explicitly says otherwise:

- `vocab/contracts.py`
- `vocab/card_contract.py`
- `vocab/models.py`
- `vocab/validators.py`
- `tests/test_contract_alignment.py`
- `DECISIONS.md`

Never modify these files merely to make another implementation or test pass.

## Validators

`vocab/validators.py` is NOT delegated to Codex.

Do not:

- implement it
- weaken it
- auto-correct data to bypass it
- add fallback behavior that silently repairs invalid data

Invalid or ambiguous data must fail closed.

## Tests

Never modify an existing test merely to make implementation pass.

If an existing test appears incorrect or conflicts with the frozen contracts:

1. stop
2. report the conflict
3. do not change the test or frozen contract

Always run the relevant tests after making a change.

For the current local repository, use the project virtual environment when available:

`.\\.venv\\Scripts\\python.exe -m pytest`

## Architecture constraints

Do not introduce:

- SQLite
- PostgreSQL
- another registry database
- an abstraction layer around AnkiConnect
- LLM calls during Anki review time
- silent retries
- silent fallback values
- automatic slug generation for approved vocabulary units

Do not automatically mutate Anki note types. In particular, do not call:

- `updateModelTemplates`
- `updateModelStyling`
- `createModel`

Deck configuration is human-owned. Never automatically create, update, delete,
or assign Anki deck option presets, and never invoke deck-config mutation
actions such as:

- `saveDeckConfig`
- `setDeckConfigId`
- `cloneDeckConfigId`
- `removeDeckConfigId`

Anki is the source of truth for vocabulary notes.

## Data safety

Never delete Anki notes as a substitute for suspension.

When lifecycle logic requires dormancy:

- suspend cards
- preserve the note
- preserve revlog
- clear only explicitly specified media fields

Never invent missing identifiers, state, model metadata, source references, or vocabulary fields.

## Task scope

Work only on the explicitly assigned task.

Before coding:

1. read the relevant normative contracts/decisions
2. read the relevant historical specification section for intent/build order
3. inspect the named context files
4. identify the files that need modification

Do not modify unrelated files.

After coding, report:

1. files changed
2. tests executed
3. test results
4. assumptions made
5. unresolved ambiguities

If requirements are ambiguous, stop instead of guessing.
