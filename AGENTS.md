\# Vocab System — Codex Instructions



Before making any change, read:



\- `docs/VOCAB\_SYSTEM\_SPEC.md`

\- the files explicitly named in the task



`docs/VOCAB\_SYSTEM\_SPEC.md` is the authoritative project specification.



\## Human-owned files



The following files are human-owned and must be treated as read-only unless the human explicitly says otherwise:



\- `vocab/contracts.py`

\- `vocab/models.py`

\- `vocab/validators.py`

\- `tests/test\_contract\_alignment.py`



Never modify these files merely to make another implementation or test pass.



\## Validators



`vocab/validators.py` is NOT delegated to Codex.



Do not:



\- implement it

\- weaken it

\- auto-correct data to bypass it

\- add fallback behavior that silently repairs invalid data



Invalid or ambiguous data must fail closed.



\## Tests



Never modify an existing test merely to make implementation pass.



If an existing test appears incorrect or conflicts with the frozen contracts:



1\. stop

2\. report the conflict

3\. do not change the test or frozen contract



Always run the relevant tests after making a change.



For the current repository, use:



`.\\.venv\\Scripts\\python.exe -m pytest`



\## Architecture constraints



Do not introduce:



\- SQLite

\- PostgreSQL

\- another registry database

\- an abstraction layer around AnkiConnect

\- LLM calls during Anki review time

\- silent retries

\- silent fallback values

\- automatic slug generation for approved vocabulary units



Anki is the source of truth for vocabulary notes.



\## Data safety



Never delete Anki notes as a substitute for suspension.



When lifecycle logic requires dormancy:



\- suspend cards

\- preserve the note

\- preserve revlog

\- clear only explicitly specified media fields



Never invent missing identifiers, state, model metadata, source references, or vocabulary fields.



\## Task scope



Work only on the explicitly assigned task.



Before coding:



1\. read the relevant specification section

2\. inspect the named context files

3\. identify the files that need modification



Do not modify unrelated files.



After coding, report:



1\. files changed

2\. tests executed

3\. test results

4\. assumptions made

5\. unresolved ambiguities



If requirements are ambiguous, stop instead of guessing.

