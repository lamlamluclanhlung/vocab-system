"""Read-only T10 corpus snapshots and pure occurrence counting."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from . import validators
from .contracts import (
    ANKI_NOTE_TYPE_NAME,
    CORPUS_EXTENSIONS,
    CORPUS_MONTH_PATTERN,
    CORPUS_REJECT_URL_PREFIXES,
    CORPUS_SCAN_VERSION,
    CORPUS_SENTENCE_TERMINATORS,
    CORPUS_SOURCE_PATTERN,
    NOTE_FIELDS,
    UNIT_KEY_PATTERN,
    UNIT_TYPE_VALUES,
)


_CORPUS_MONTH_RE = re.compile(CORPUS_MONTH_PATTERN)
_CORPUS_SOURCE_RE = re.compile(CORPUS_SOURCE_PATTERN)
_UNIT_KEY_RE = re.compile(UNIT_KEY_PATTERN)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SENTENCE_BOUNDARY_PATTERN = (
    "[" + "".join(re.escape(value) for value in CORPUS_SENTENCE_TERMINATORS) + "]+"
)
_NEWLINE_SEQUENCE_PATTERN = r"(?:\r\n|\r(?!\n)|(?<!\r)\n)"
_BLANK_LINE_BOUNDARY_PATTERN = (
    rf"{_NEWLINE_SEQUENCE_PATTERN}[ \t]*{_NEWLINE_SEQUENCE_PATTERN}"
)
_BLOCK_BOUNDARY_RE = re.compile(
    rf"(?:{_SENTENCE_BOUNDARY_PATTERN}|{_BLANK_LINE_BOUNDARY_PATTERN})"
)


class CorpusScanError(RuntimeError):
    """Base class for deterministic T10 snapshot and counting failures."""


class CorpusRegistryError(CorpusScanError):
    """Raised when the Anki registry snapshot is malformed or ambiguous."""


class CorpusSnapshotError(CorpusScanError):
    """Raised when caller identity or a corpus artifact is invalid."""


class CorpusCountError(CorpusScanError):
    """Raised when direct pure-counting input is structurally invalid."""


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    unit_key: str
    lemma: str
    unit_type: str


@dataclass(frozen=True, slots=True)
class CorpusFileSnapshot:
    path: str
    sha256: str
    blocks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusSnapshot:
    source: str
    month: str
    files: tuple[CorpusFileSnapshot, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class CorpusCount:
    unit_key: str
    lemma: str
    unit_type: str
    count: int


@dataclass(frozen=True, slots=True)
class CorpusScanResult:
    source: str
    month: str
    corpus_snapshot_digest: str
    corpus_file_count: int
    counts: tuple[CorpusCount, ...]


class _RegistryReader(Protocol):
    def find_notes(self, query: str) -> object: ...

    def notes_info(self, note_ids: list[int]) -> object: ...


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _corpus_digest(files: tuple[CorpusFileSnapshot, ...]) -> str:
    identity = {
        "scan_version": CORPUS_SCAN_VERSION,
        "files": [
            {
                "path": file.path,
                "sha256": file.sha256,
            }
            for file in files
        ],
    }
    return sha256(_canonical_json_bytes(identity)).hexdigest()


def _split_blocks(decoded_text: str) -> tuple[str, ...]:
    return tuple(
        fragment
        for fragment in _BLOCK_BOUNDARY_RE.split(decoded_text)
        if fragment.strip()
    )


def _valid_source(value: object) -> bool:
    return type(value) is str and _CORPUS_SOURCE_RE.fullmatch(value) is not None


def _valid_month(value: object) -> bool:
    return type(value) is str and _CORPUS_MONTH_RE.fullmatch(value) is not None


def _valid_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _valid_canonical_file_name(value: object) -> bool:
    return (
        type(value) is str
        and value not in ("", ".", "..")
        and "/" not in value
        and "\\" not in value
        and Path(value).suffix.casefold() in CORPUS_EXTENSIONS
    )


def _validate_registry_entry(entry: RegistryEntry) -> None:
    if (
        type(entry.unit_key) is not str
        or _UNIT_KEY_RE.fullmatch(entry.unit_key) is None
    ):
        raise CorpusCountError("registry entry has an invalid unit_key")
    if type(entry.lemma) is not str or not entry.lemma.strip():
        raise CorpusCountError("registry entry has a blank lemma")
    if type(entry.unit_type) is not str or entry.unit_type not in UNIT_TYPE_VALUES:
        raise CorpusCountError("registry entry has an invalid unit_type")
    try:
        validators.unit_match_spans("", entry.lemma, entry.unit_type)
    except ValueError as exc:
        raise CorpusCountError("registry entry has an invalid D19 Unit shape") from exc


def read_registry_snapshot(anki: _RegistryReader) -> tuple[RegistryEntry, ...]:
    """Read one immutable lexical snapshot of all VocabularyUnit notes."""
    note_ids = anki.find_notes(f"note:{ANKI_NOTE_TYPE_NAME}")
    if not isinstance(note_ids, list):
        raise CorpusRegistryError("find_notes result must be a list")
    if any(type(note_id) is not int for note_id in note_ids):
        raise CorpusRegistryError("find_notes result must contain actual integers")
    if len(note_ids) != len(set(note_ids)):
        raise CorpusRegistryError("find_notes returned duplicate note IDs")
    if not note_ids:
        return ()

    requested_ids = sorted(note_ids)
    notes = anki.notes_info(requested_ids)
    if not isinstance(notes, list):
        raise CorpusRegistryError("notes_info result must be a list")
    if len(notes) != len(requested_ids):
        raise CorpusRegistryError("notes_info result has incorrect cardinality")

    requested_id_set = set(requested_ids)
    returned_ids: set[int] = set()
    entries: list[RegistryEntry] = []
    unit_keys: set[str] = set()

    for note in notes:
        if not isinstance(note, Mapping):
            raise CorpusRegistryError("notes_info entries must be objects")
        note_id = note.get("noteId")
        if type(note_id) is not int or note_id not in requested_id_set:
            raise CorpusRegistryError("notes_info returned a foreign or invalid note ID")
        if note_id in returned_ids:
            raise CorpusRegistryError("notes_info returned a duplicate note ID")
        returned_ids.add(note_id)

        if note.get("modelName") != ANKI_NOTE_TYPE_NAME:
            raise CorpusRegistryError("registry note has the wrong modelName")
        fields = note.get("fields")
        if not isinstance(fields, Mapping) or set(fields) != set(NOTE_FIELDS):
            raise CorpusRegistryError("registry note fields must exactly match NOTE_FIELDS")

        values: dict[str, str] = {}
        for field_name in NOTE_FIELDS:
            field = fields[field_name]
            if not isinstance(field, Mapping) or "value" not in field:
                raise CorpusRegistryError(
                    f"registry field {field_name!r} must contain a value"
                )
            value = field["value"]
            if not isinstance(value, str):
                raise CorpusRegistryError(
                    f"registry field {field_name!r} value must be a string"
                )
            values[field_name] = value

        unit_key = values["unit_key"]
        lemma = values["lemma"]
        unit_type = values["unit_type"]
        if _UNIT_KEY_RE.fullmatch(unit_key) is None:
            raise CorpusRegistryError("registry note has an invalid unit_key")
        if not lemma.strip():
            raise CorpusRegistryError("registry note has a blank lemma")
        if unit_type not in UNIT_TYPE_VALUES:
            raise CorpusRegistryError("registry note has an invalid unit_type")
        try:
            validators.unit_match_spans("", lemma, unit_type)
        except ValueError as exc:
            raise CorpusRegistryError(
                "registry note has an invalid D19 Unit shape"
            ) from exc
        if unit_key in unit_keys:
            raise CorpusRegistryError("registry contains a duplicate unit_key")
        unit_keys.add(unit_key)
        entries.append(
            RegistryEntry(
                unit_key=unit_key,
                lemma=lemma,
                unit_type=unit_type,
            )
        )

    if returned_ids != requested_id_set:
        raise CorpusRegistryError("notes_info did not return every requested note ID")
    return tuple(sorted(entries, key=lambda entry: entry.unit_key))


def read_corpus_snapshot(
    corpus_root: str | Path,
    *,
    source: str,
    month: str,
) -> CorpusSnapshot:
    """Read, validate, and identify one flat local plaintext corpus."""
    if not _valid_source(source):
        raise CorpusSnapshotError("source must match CORPUS_SOURCE_PATTERN")
    if not _valid_month(month):
        raise CorpusSnapshotError("month must match CORPUS_MONTH_PATTERN")

    month_directory = Path(corpus_root) / month
    if not month_directory.exists() or not month_directory.is_dir():
        raise CorpusSnapshotError("corpus month directory must exist")

    discovered: list[Path] = []
    canonical_names: set[str] = set()
    for entry in month_directory.iterdir():
        if entry.is_symlink():
            raise CorpusSnapshotError("corpus direct children must not be symlinks")
        if entry.is_dir():
            raise CorpusSnapshotError("corpus direct children must not be directories")
        if not entry.is_file():
            raise CorpusSnapshotError("corpus direct children must be regular files")
        if entry.suffix.casefold() not in CORPUS_EXTENSIONS:
            raise CorpusSnapshotError("corpus direct child has an unsupported extension")
        if entry.name in canonical_names:
            raise CorpusSnapshotError("corpus has duplicate canonical filenames")
        canonical_names.add(entry.name)
        discovered.append(entry)

    files: list[CorpusFileSnapshot] = []
    for path in sorted(discovered, key=lambda entry: entry.name):
        raw = path.read_bytes()
        file_sha256 = sha256(raw).hexdigest()
        try:
            decoded_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CorpusSnapshotError("corpus file is not valid UTF-8") from exc
        casefolded_text = decoded_text.casefold()
        if any(prefix in casefolded_text for prefix in CORPUS_REJECT_URL_PREFIXES):
            raise CorpusSnapshotError("corpus file contains a rejected URL prefix")
        files.append(
            CorpusFileSnapshot(
                path=path.name,
                sha256=file_sha256,
                blocks=_split_blocks(decoded_text),
            )
        )

    frozen_files = tuple(files)
    return CorpusSnapshot(
        source=source,
        month=month,
        files=frozen_files,
        digest=_corpus_digest(frozen_files),
    )


def count_unit_occurrences(block: str, lemma: str, unit_type: str) -> int:
    """Count leftmost canonical non-overlapping Unit spans in one block."""
    spans = validators.unit_match_spans(block, lemma, unit_type)
    count = 0
    next_unconsumed = 0
    for start, end in spans:
        if start < next_unconsumed:
            continue
        count += 1
        next_unconsumed = end + 1
    return count


def count_scan(
    registry: tuple[RegistryEntry, ...],
    corpus: CorpusSnapshot,
) -> CorpusScanResult:
    """Purely count a canonical registry against a frozen corpus snapshot."""
    if type(registry) is not tuple:
        raise CorpusCountError("registry must be an actual tuple")

    previous_unit_key: str | None = None
    unit_keys: set[str] = set()
    for entry in registry:
        if type(entry) is not RegistryEntry:
            raise CorpusCountError("registry must contain RegistryEntry values only")
        _validate_registry_entry(entry)
        if entry.unit_key in unit_keys:
            raise CorpusCountError("registry unit_key values must be unique")
        if previous_unit_key is not None and entry.unit_key <= previous_unit_key:
            raise CorpusCountError("registry must be strictly ordered by unit_key")
        unit_keys.add(entry.unit_key)
        previous_unit_key = entry.unit_key

    if type(corpus) is not CorpusSnapshot:
        raise CorpusCountError("corpus must be a CorpusSnapshot")
    if not _valid_source(corpus.source) or not _valid_month(corpus.month):
        raise CorpusCountError("corpus source or month is invalid")
    if type(corpus.files) is not tuple:
        raise CorpusCountError("corpus files must be an actual tuple")

    previous_path: str | None = None
    file_paths: set[str] = set()
    for file in corpus.files:
        if type(file) is not CorpusFileSnapshot:
            raise CorpusCountError(
                "corpus files must contain CorpusFileSnapshot values only"
            )
        if not _valid_canonical_file_name(file.path):
            raise CorpusCountError("corpus file path is not a canonical filename")
        if file.path in file_paths:
            raise CorpusCountError("corpus file paths must be unique")
        if previous_path is not None and file.path <= previous_path:
            raise CorpusCountError("corpus files must be strictly ordered by path")
        if not _valid_sha256(file.sha256):
            raise CorpusCountError("corpus file has an invalid SHA-256 digest")
        if type(file.blocks) is not tuple or any(
            type(block) is not str for block in file.blocks
        ):
            raise CorpusCountError("corpus file blocks must be an actual tuple of strings")
        file_paths.add(file.path)
        previous_path = file.path

    if not _valid_sha256(corpus.digest):
        raise CorpusCountError("corpus has an invalid SHA-256 digest")
    if _corpus_digest(corpus.files) != corpus.digest:
        raise CorpusCountError("corpus digest does not match file metadata")

    counts: list[CorpusCount] = []
    for entry in registry:
        total = 0
        for file in corpus.files:
            for block in file.blocks:
                total += count_unit_occurrences(
                    block,
                    entry.lemma,
                    entry.unit_type,
                )
        counts.append(
            CorpusCount(
                unit_key=entry.unit_key,
                lemma=entry.lemma,
                unit_type=entry.unit_type,
                count=total,
            )
        )

    return CorpusScanResult(
        source=corpus.source,
        month=corpus.month,
        corpus_snapshot_digest=corpus.digest,
        corpus_file_count=len(corpus.files),
        counts=tuple(counts),
    )
