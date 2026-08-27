from __future__ import annotations

import ast
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Callable

import pytest

import vocab.corpus as corpus_module
from vocab.contracts import (
    ANKI_NOTE_TYPE_NAME,
    CORPUS_SCAN_VERSION,
    EVENT_SCHEMA_VERSION,
    NOTE_FIELDS,
    T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS,
    T10_ENCOUNTER_PRODUCER_ID,
)
from vocab.corpus import (
    CorpusCount,
    CorpusCountError,
    CorpusEmissionError,
    CorpusEmitReport,
    CorpusEncounterError,
    CorpusFileSnapshot,
    CorpusHistoryError,
    CorpusRegistryError,
    CorpusScanResult,
    CorpusSnapshot,
    CorpusSnapshotError,
    EncounterPlan,
    RegistryEntry,
    build_encounter_plans,
    count_scan,
    count_unit_occurrences,
    emit_scan,
    read_corpus_snapshot,
    read_registry_snapshot,
)
from vocab.models import Event


def note_fields(**overrides: object) -> dict[str, dict[str, object]]:
    values: dict[str, object] = {field_name: "" for field_name in NOTE_FIELDS}
    values.update(
        {
            "unit_key": "art::creative-work",
            "lemma": "art",
            "unit_type": "word",
        }
    )
    values.update(overrides)
    return {
        field_name: {"value": values[field_name]}
        for field_name in NOTE_FIELDS
    }


def note_record(
    note_id: int,
    **field_overrides: object,
) -> dict[str, object]:
    return {
        "noteId": note_id,
        "modelName": ANKI_NOTE_TYPE_NAME,
        "fields": note_fields(**field_overrides),
    }


class FakeAnki:
    def __init__(self, note_ids: object, notes: object) -> None:
        self.note_ids = note_ids
        self.notes = notes
        self.find_queries: list[str] = []
        self.notes_info_calls: list[list[int]] = []
        self.write_calls: list[tuple[str, object]] = []

    def find_notes(self, query: str) -> object:
        self.find_queries.append(query)
        return self.note_ids

    def notes_info(self, note_ids: list[int]) -> object:
        self.notes_info_calls.append(list(note_ids))
        return self.notes

    def add_notes(self, value: object) -> None:
        self.write_calls.append(("add_notes", value))

    def update_note_fields(self, value: object) -> None:
        self.write_calls.append(("update_note_fields", value))

    def suspend(self, value: object) -> None:
        self.write_calls.append(("suspend", value))

    def unsuspend(self, value: object) -> None:
        self.write_calls.append(("unsuspend", value))


def canonical_digest(files: tuple[CorpusFileSnapshot, ...]) -> str:
    value = {
        "scan_version": CORPUS_SCAN_VERSION,
        "files": [
            {"path": file.path, "sha256": file.sha256}
            for file in files
        ],
    }
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def file_snapshot(
    path: str,
    *blocks: str,
    digest_seed: str | None = None,
) -> CorpusFileSnapshot:
    seed = path if digest_seed is None else digest_seed
    return CorpusFileSnapshot(
        path=path,
        sha256=sha256(seed.encode("utf-8")).hexdigest(),
        blocks=tuple(blocks),
    )


def corpus_snapshot(
    *files: CorpusFileSnapshot,
    source: str = "reading",
    month: str = "2026-08",
) -> CorpusSnapshot:
    frozen_files = tuple(files)
    return CorpusSnapshot(
        source=source,
        month=month,
        files=frozen_files,
        digest=canonical_digest(frozen_files),
    )


def month_directory(tmp_path: Path, month: str = "2026-08") -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    directory = root / month
    directory.mkdir(parents=True)
    return root, directory


def snapshot_text(tmp_path: Path, text: str) -> CorpusSnapshot:
    root, directory = month_directory(tmp_path)
    (directory / "sample.txt").write_bytes(text.encode("utf-8"))
    return read_corpus_snapshot(root, source="reading", month="2026-08")


def count_snapshot_blocks(
    snapshot: CorpusSnapshot,
    lemma: str,
    unit_type: str,
) -> int:
    return sum(
        count_unit_occurrences(block, lemma, unit_type)
        for file in snapshot.files
        for block in file.blocks
    )


def scan_result(
    *counts: CorpusCount,
    source: str = "reading",
    month: str = "2026-08",
    corpus_snapshot_digest: str | None = None,
    corpus_file_count: int = 2,
) -> CorpusScanResult:
    digest = (
        sha256(b"frozen corpus").hexdigest()
        if corpus_snapshot_digest is None
        else corpus_snapshot_digest
    )
    return CorpusScanResult(
        source=source,
        month=month,
        corpus_snapshot_digest=digest,
        corpus_file_count=corpus_file_count,
        counts=tuple(counts),
    )


def word_count(name: str, count: int = 1) -> CorpusCount:
    return CorpusCount(
        unit_key=f"{name}::sense",
        lemma=name,
        unit_type="word",
        count=count,
    )


def expected_encounter_id(
    unit_key: str,
    *,
    source: str = "reading",
    month: str = "2026-08",
) -> str:
    canonical = json.dumps(
        {
            "producer": T10_ENCOUNTER_PRODUCER_ID,
            "scan_version": CORPUS_SCAN_VERSION,
            "source": source,
            "month": month,
            "unit_key": unit_key,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def event_from_plan(
    plan: EncounterPlan,
    *,
    payload_updates: dict[str, object] | None = None,
    event_updates: dict[str, object] | None = None,
) -> Event:
    payload = dict(plan.payload)
    if payload_updates is not None:
        payload.update(payload_updates)
    values: dict[str, object] = {
        "v": EVENT_SCHEMA_VERSION,
        "ts": "2026-08-23T00:00:00+00:00",
        "day": "2026-08-23",
        "event": "ENCOUNTER",
        "unit_key": plan.unit_key,
        "payload": payload,
    }
    if event_updates is not None:
        values.update(event_updates)
    return Event(**values)  # type: ignore[arg-type]


class FakeEventLog:
    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        read_result: object | None = None,
        read_error: Exception | None = None,
        fail_on_append: int | None = None,
        return_transform: Callable[[Event], object] | None = None,
    ) -> None:
        self.events = [] if events is None else list(events)
        self.read_result = read_result
        self.read_error = read_error
        self.fail_on_append = fail_on_append
        self.return_transform = return_transform
        self.read_calls = 0
        self.log_calls: list[tuple[str, str, dict[str, object]]] = []

    def read(self) -> object:
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        if self.read_result is not None:
            return self.read_result
        return list(self.events)

    def log(
        self,
        event: str,
        unit_key: str,
        payload: dict[str, object],
    ) -> object:
        self.log_calls.append((event, unit_key, dict(payload)))
        if self.fail_on_append == len(self.log_calls):
            raise OSError("injected append failure")
        stored = Event(
            v=EVENT_SCHEMA_VERSION,
            ts="2026-08-23T00:00:00+00:00",
            day="2026-08-23",
            event=event,
            unit_key=unit_key,
            payload=dict(payload),
        )
        self.events.append(stored)
        if self.return_transform is not None:
            return self.return_transform(stored)
        return stored


def test_registry_snapshot_reads_once_and_returns_unit_key_order() -> None:
    anki = FakeAnki(
        [20, 10],
        [
            note_record(
                20,
                unit_key="pose-a-threat-to::create-danger",
                lemma="pose a threat to",
                unit_type="chunk",
            ),
            note_record(10),
        ],
    )

    assert read_registry_snapshot(anki) == (
        RegistryEntry("art::creative-work", "art", "word"),
        RegistryEntry(
            "pose-a-threat-to::create-danger",
            "pose a threat to",
            "chunk",
        ),
    )
    assert anki.find_queries == ["note:VocabularyUnit"]
    assert anki.notes_info_calls == [[10, 20]]
    assert anki.write_calls == []


def test_empty_registry_skips_notes_info() -> None:
    anki = FakeAnki([], object())

    assert read_registry_snapshot(anki) == ()
    assert anki.find_queries == ["note:VocabularyUnit"]
    assert anki.notes_info_calls == []
    assert anki.write_calls == []


@pytest.mark.parametrize("result", [None, (), {}, "bad"])
def test_registry_requires_find_notes_list(result: object) -> None:
    with pytest.raises(CorpusRegistryError, match="must be a list"):
        read_registry_snapshot(FakeAnki(result, []))


@pytest.mark.parametrize("note_ids", [[True], [1.0], ["1"]])
def test_registry_note_ids_must_be_actual_integers(note_ids: list[object]) -> None:
    with pytest.raises(CorpusRegistryError, match="actual integers"):
        read_registry_snapshot(FakeAnki(note_ids, []))


def test_registry_rejects_duplicate_discovered_note_ids() -> None:
    with pytest.raises(CorpusRegistryError, match="duplicate note IDs"):
        read_registry_snapshot(FakeAnki([1, 1], []))


@pytest.mark.parametrize("notes", [None, (), {}, "bad"])
def test_registry_requires_notes_info_list(notes: object) -> None:
    with pytest.raises(CorpusRegistryError, match="must be a list"):
        read_registry_snapshot(FakeAnki([1], notes))


def test_registry_rejects_notes_info_cardinality_mismatch() -> None:
    with pytest.raises(CorpusRegistryError, match="cardinality"):
        read_registry_snapshot(FakeAnki([1, 2], [note_record(1)]))


def test_registry_rejects_non_mapping_note() -> None:
    with pytest.raises(CorpusRegistryError, match="must be objects"):
        read_registry_snapshot(FakeAnki([1], [None]))


def test_registry_rejects_foreign_note_id() -> None:
    with pytest.raises(CorpusRegistryError, match="foreign or invalid"):
        read_registry_snapshot(FakeAnki([1], [note_record(2)]))


def test_registry_rejects_duplicate_returned_note_id() -> None:
    with pytest.raises(CorpusRegistryError, match="duplicate note ID"):
        read_registry_snapshot(
            FakeAnki([1, 2], [note_record(1), note_record(1)])
        )


def test_registry_rejects_wrong_model_name() -> None:
    note = note_record(1)
    note["modelName"] = "Other"

    with pytest.raises(CorpusRegistryError, match="wrong modelName"):
        read_registry_snapshot(FakeAnki([1], [note]))


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_registry_requires_exact_note_field_keyset(change: str) -> None:
    note = note_record(1)
    fields = note["fields"]
    assert isinstance(fields, dict)
    if change == "missing":
        del fields["audio_3"]
    else:
        fields["extra"] = {"value": ""}

    with pytest.raises(CorpusRegistryError, match="exactly match NOTE_FIELDS"):
        read_registry_snapshot(FakeAnki([1], [note]))


def test_registry_requires_field_record_value_key() -> None:
    note = note_record(1)
    fields = note["fields"]
    assert isinstance(fields, dict)
    fields["lemma"] = {}

    with pytest.raises(CorpusRegistryError, match="must contain a value"):
        read_registry_snapshot(FakeAnki([1], [note]))


def test_registry_requires_string_field_values() -> None:
    note = note_record(1)
    fields = note["fields"]
    assert isinstance(fields, dict)
    fields["Ctx_5"] = {"value": 5}

    with pytest.raises(CorpusRegistryError, match="must be a string"):
        read_registry_snapshot(FakeAnki([1], [note]))


@pytest.mark.parametrize(
    ("field_overrides", "message"),
    [
        ({"unit_key": "INVALID"}, "invalid unit_key"),
        ({"lemma": " \t "}, "blank lemma"),
        ({"unit_type": "phrase"}, "invalid unit_type"),
        (
            {"lemma": "two tokens", "unit_type": "word"},
            "invalid D19 Unit shape",
        ),
    ],
)
def test_registry_rejects_invalid_lexical_entry(
    field_overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CorpusRegistryError, match=message):
        read_registry_snapshot(
            FakeAnki([1], [note_record(1, **field_overrides)])
        )


def test_registry_rejects_duplicate_unit_key_across_notes() -> None:
    with pytest.raises(CorpusRegistryError, match="duplicate unit_key"):
        read_registry_snapshot(
            FakeAnki([1, 2], [note_record(1), note_record(2)])
        )


def test_registry_ignores_unrelated_lifecycle_context_and_media_semantics() -> None:
    note = note_record(
        1,
        Target_R="not-a-target",
        state_R="not-a-state",
        definition_en="",
        Ctx_1="https://not-corpus-validation.example",
        audio_1="opaque media text",
        VisualCue="anything",
    )

    assert read_registry_snapshot(FakeAnki([1], [note])) == (
        RegistryEntry("art::creative-work", "art", "word"),
    )


@pytest.mark.parametrize("month", ["2026-00", "2026-13", "2026-1", "26-01", 1])
def test_corpus_snapshot_rejects_invalid_month(tmp_path: Path, month: object) -> None:
    with pytest.raises(CorpusSnapshotError, match="month"):
        read_corpus_snapshot(tmp_path, source="reading", month=month)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "source",
    ["Reading", "own writing", "-reading", "reading-", "reading--notes", 1],
)
def test_corpus_snapshot_rejects_invalid_source(tmp_path: Path, source: object) -> None:
    with pytest.raises(CorpusSnapshotError, match="source"):
        read_corpus_snapshot(tmp_path, source=source, month="2026-08")  # type: ignore[arg-type]


def test_corpus_snapshot_requires_existing_month_directory(tmp_path: Path) -> None:
    with pytest.raises(CorpusSnapshotError, match="must exist"):
        read_corpus_snapshot(tmp_path, source="reading", month="2026-08")


def test_corpus_snapshot_rejects_month_path_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "2026-08").write_text("not a directory", encoding="utf-8")

    with pytest.raises(CorpusSnapshotError, match="must exist"):
        read_corpus_snapshot(tmp_path, source="reading", month="2026-08")


def test_empty_corpus_directory_is_a_valid_snapshot(tmp_path: Path) -> None:
    root, _directory = month_directory(tmp_path)

    snapshot = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert snapshot.files == ()
    assert snapshot.digest == canonical_digest(())


def test_txt_extensions_are_case_insensitive(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    (directory / "lower.txt").write_text("art", encoding="utf-8")
    (directory / "upper.TXT").write_text("art", encoding="utf-8")

    snapshot = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert tuple(file.path for file in snapshot.files) == ("lower.txt", "upper.TXT")


@pytest.mark.parametrize("filename", ["notes.md", "notes.bin", "README"])
def test_corpus_snapshot_rejects_unsupported_regular_files(
    tmp_path: Path,
    filename: str,
) -> None:
    root, directory = month_directory(tmp_path)
    (directory / filename).write_text("art", encoding="utf-8")

    with pytest.raises(CorpusSnapshotError, match="unsupported extension"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_corpus_snapshot_rejects_nested_directory(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    (directory / "nested").mkdir()

    with pytest.raises(CorpusSnapshotError, match="must not be directories"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_corpus_snapshot_rejects_symlink_policy_without_os_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory = month_directory(tmp_path)
    selected = directory / "selected.txt"
    selected.write_text("art", encoding="utf-8")
    original = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == selected or original(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    with pytest.raises(CorpusSnapshotError, match="must not be symlinks"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_corpus_snapshot_rejects_non_regular_direct_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory = month_directory(tmp_path)
    selected = directory / "selected.txt"
    selected.write_text("art", encoding="utf-8")
    original = Path.is_file

    def fake_is_file(path: Path) -> bool:
        return False if path == selected else original(path)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    with pytest.raises(CorpusSnapshotError, match="regular files"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_corpus_snapshot_rejects_duplicate_exact_canonical_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory = month_directory(tmp_path)
    selected = directory / "selected.txt"
    selected.write_text("art", encoding="utf-8")
    original = Path.iterdir

    def duplicate_iterdir(path: Path):
        if path == directory:
            return iter((selected, selected))
        return original(path)

    monkeypatch.setattr(Path, "iterdir", duplicate_iterdir)

    with pytest.raises(CorpusSnapshotError, match="duplicate canonical"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_corpus_filenames_use_exact_lexical_order_and_preserve_case(
    tmp_path: Path,
) -> None:
    root, directory = month_directory(tmp_path)
    names = ("z.txt", "A.txt", "b.txt", "É.txt")
    for name in names:
        (directory / name).write_text(name, encoding="utf-8")

    snapshot = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert tuple(file.path for file in snapshot.files) == tuple(sorted(names))
    assert all("/" not in file.path and "\\" not in file.path for file in snapshot.files)


def test_corpus_snapshot_rejects_invalid_utf8(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    (directory / "bad.txt").write_bytes(b"\xff")

    with pytest.raises(CorpusSnapshotError, match="valid UTF-8"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_utf8_bom_is_removed_from_lexical_blocks(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    (directory / "bom.txt").write_bytes(b"\xef\xbb\xbfart")

    snapshot = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert snapshot.files[0].blocks == ("art",)


def test_bom_changes_raw_file_and_corpus_identity(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    path = directory / "sample.txt"
    path.write_bytes(b"art")
    without_bom = read_corpus_snapshot(root, source="reading", month="2026-08")
    path.write_bytes(b"\xef\xbb\xbfart")
    with_bom = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert without_bom.files[0].sha256 != with_bom.files[0].sha256
    assert without_bom.digest != with_bom.digest
    assert without_bom.files[0].blocks == with_bom.files[0].blocks == ("art",)


def test_lf_and_crlf_raw_bytes_have_different_identity(tmp_path: Path) -> None:
    root, directory = month_directory(tmp_path)
    path = directory / "sample.txt"
    path.write_bytes(b"pose a\nthreat to")
    lf = read_corpus_snapshot(root, source="reading", month="2026-08")
    path.write_bytes(b"pose a\r\nthreat to")
    crlf = read_corpus_snapshot(root, source="reading", month="2026-08")

    assert lf.files[0].sha256 != crlf.files[0].sha256
    assert lf.digest != crlf.digest


def test_source_and_month_do_not_enter_corpus_digest(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    for month in ("2026-07", "2026-08"):
        directory = root / month
        directory.mkdir(parents=True)
        (directory / "same.txt").write_bytes(b"same exact bytes")

    first = read_corpus_snapshot(root, source="reading", month="2026-07")
    second = read_corpus_snapshot(root, source="own-writing", month="2026-08")

    assert first.digest == second.digest


@pytest.mark.parametrize("prefix", ["HTTP://", "HTTPS://", "WWW."])
def test_url_prefix_rejection_is_case_insensitive_literal(
    tmp_path: Path,
    prefix: str,
) -> None:
    root, directory = month_directory(tmp_path)
    (directory / "url.txt").write_text(
        f"ordinary prose {prefix}example.invalid tail",
        encoding="utf-8",
    )

    with pytest.raises(CorpusSnapshotError, match="rejected URL prefix"):
        read_corpus_snapshot(root, source="reading", month="2026-08")


def test_words_http_and_www_without_frozen_literals_are_allowed(tmp_path: Path) -> None:
    snapshot = snapshot_text(tmp_path, "The words http and www are ordinary here.")

    assert snapshot.files[0].blocks == ("The words http and www are ordinary here",)


def test_each_corpus_file_is_read_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory = month_directory(tmp_path)
    for name in ("a.txt", "b.txt"):
        (directory / name).write_text(name, encoding="utf-8")
    original = Path.read_bytes
    calls: list[str] = []

    def tracked_read_bytes(path: Path) -> bytes:
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    read_corpus_snapshot(root, source="reading", month="2026-08")

    assert calls == ["a.txt", "b.txt"]


def test_sentence_terminator_splits_blocks_and_prevents_crossing(tmp_path: Path) -> None:
    snapshot = snapshot_text(tmp_path, "He did pose. A threat to health.")

    assert snapshot.files[0].blocks == ("He did pose", " A threat to health")
    assert count_snapshot_blocks(snapshot, "pose a threat to", "chunk") == 0


def test_single_newline_stays_inside_one_block(tmp_path: Path) -> None:
    snapshot = snapshot_text(tmp_path, "pose a\nthreat to")

    assert snapshot.files[0].blocks == ("pose a\nthreat to",)
    assert count_snapshot_blocks(snapshot, "pose a threat to", "chunk") == 1


@pytest.mark.parametrize("separator", ["\n\n", "\n   \n", "\r\n\t\r\n", "\r\r"])
def test_blank_line_splits_blocks(
    tmp_path: Path,
    separator: str,
) -> None:
    snapshot = snapshot_text(tmp_path, f"pose a{separator}threat to")

    assert snapshot.files[0].blocks == ("pose a", "threat to")
    assert count_snapshot_blocks(snapshot, "pose a threat to", "chunk") == 0


def test_repeated_sentence_terminators_make_deterministic_blocks(tmp_path: Path) -> None:
    snapshot = snapshot_text(tmp_path, "Wow?! Really… Yes.")

    assert snapshot.files[0].blocks == ("Wow", " Really", " Yes")


def test_whitespace_only_fragments_are_discarded(tmp_path: Path) -> None:
    snapshot = snapshot_text(tmp_path, "  ... \n\n\t ?!  ")

    assert snapshot.files[0].blocks == ()


def test_word_occurrence_count_is_token_based() -> None:
    assert count_unit_occurrences("art art partial art", "art", "word") == 3


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("pose a threat to", 1),
        ("pose a serious threat to", 1),
        ("pose a very serious threat to", 1),
        ("pose a very immediate serious threat to", 0),
    ],
)
def test_chunk_occurrence_count_respects_insertion_budget(
    block: str,
    expected: int,
) -> None:
    assert count_unit_occurrences(block, "pose a threat to", "chunk") == expected


def test_overlapping_chunk_starts_count_once() -> None:
    assert (
        count_unit_occurrences(
            "pose a pose a threat to",
            "pose a threat to",
            "chunk",
        )
        == 1
    )


def test_non_overlapping_chunk_repeats_count_twice() -> None:
    assert (
        count_unit_occurrences(
            "pose a threat to and pose a threat to",
            "pose a threat to",
            "chunk",
        )
        == 2
    )


@pytest.mark.parametrize(
    ("slot", "expected"),
    [
        ("one", 1),
        ("one two three four five six", 1),
        ("one two three four five six seven", 0),
    ],
)
def test_frame_occurrence_count_respects_slot_bounds(slot: str, expected: int) -> None:
    assert (
        count_unit_occurrences(
            f"it is {slot} that",
            "it is ___ that",
            "frame",
        )
        == expected
    )


def test_overlapping_frame_starts_count_once() -> None:
    assert (
        count_unit_occurrences(
            "it is that it is really that",
            "it is ___ that",
            "frame",
        )
        == 1
    )


def test_sentence_boundary_prevents_chunk_match(tmp_path: Path) -> None:
    snapshot = snapshot_text(
        tmp_path,
        "He did pose. A threat to public health emerged.",
    )

    assert count_snapshot_blocks(snapshot, "pose a threat to", "chunk") == 0


def test_counting_consumes_shared_validator_span_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_spans(block: str, lemma: str, unit_type: str):
        calls.append((block, lemma, unit_type))
        return ((0, 1), (1, 2), (3, 3))

    monkeypatch.setattr(corpus_module.validators, "unit_match_spans", fake_spans)

    assert count_unit_occurrences("block", "lemma", "word") == 2
    assert calls == [("block", "lemma", "word")]


def test_corpus_module_has_no_copied_matcher_or_persistence_architecture() -> None:
    source = inspect.getsource(corpus_module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "vocab.events" not in imported_modules
    assert "events" not in imported_modules
    assert imported_modules.isdisjoint(
        {
            "random",
            "time",
            "datetime",
            "requests",
            "urllib",
            "sqlite3",
            "reconcile",
            "vocab.reconcile",
        }
    )
    for forbidden in (
        "update_note_fields(",
        "add_notes(",
        "suspend(",
        "unsuspend(",
        "LEXICAL_TOKEN_PATTERN",
        "_contains_chunk",
        "_contains_frame",
        "FRAME_PLACEHOLDER",
    ):
        assert forbidden not in source
    for function in (
        read_registry_snapshot,
        read_corpus_snapshot,
        count_unit_occurrences,
        count_scan,
    ):
        assert "event_log" not in inspect.signature(function).parameters


def test_count_scan_aggregates_files_and_blocks_in_unit_key_order() -> None:
    registry = (
        RegistryEntry("art::creative-work", "art", "word"),
        RegistryEntry(
            "pose-a-threat-to::create-danger",
            "pose a threat to",
            "chunk",
        ),
        RegistryEntry("zero::absent", "zero", "word"),
    )
    corpus = corpus_snapshot(
        file_snapshot("a.txt", "art art", "pose a threat to"),
        file_snapshot("b.txt", "art", "pose a serious threat to"),
    )

    result = count_scan(registry, corpus)

    assert result == CorpusScanResult(
        source="reading",
        month="2026-08",
        corpus_snapshot_digest=corpus.digest,
        corpus_file_count=2,
        counts=(
            CorpusCount("art::creative-work", "art", "word", 3),
            CorpusCount(
                "pose-a-threat-to::create-danger",
                "pose a threat to",
                "chunk",
                2,
            ),
            CorpusCount("zero::absent", "zero", "word", 0),
        ),
    )


def test_count_scan_empty_corpus_emits_one_zero_per_registry_entry() -> None:
    registry = (
        RegistryEntry("art::creative-work", "art", "word"),
        RegistryEntry("zero::absent", "zero", "word"),
    )
    corpus = corpus_snapshot()

    result = count_scan(registry, corpus)

    assert tuple(count.count for count in result.counts) == (0, 0)
    assert result.corpus_file_count == 0


def test_count_scan_empty_registry_returns_empty_counts() -> None:
    corpus = corpus_snapshot(file_snapshot("a.txt", "art"))

    assert count_scan((), corpus).counts == ()


def test_count_scan_is_deterministic_and_does_not_mutate_inputs() -> None:
    registry = (RegistryEntry("art::creative-work", "art", "word"),)
    corpus = corpus_snapshot(file_snapshot("a.txt", "art art"))
    registry_before = deepcopy(registry)
    corpus_before = deepcopy(corpus)

    first = count_scan(registry, corpus)
    second = count_scan(registry, corpus)

    assert first == second
    assert registry == registry_before
    assert corpus == corpus_before


def test_count_scan_rejects_registry_that_is_not_an_actual_tuple() -> None:
    with pytest.raises(CorpusCountError, match="actual tuple"):
        count_scan([], corpus_snapshot())  # type: ignore[arg-type]


def test_count_scan_rejects_unsorted_registry() -> None:
    registry = (
        RegistryEntry("zero::absent", "zero", "word"),
        RegistryEntry("art::creative-work", "art", "word"),
    )

    with pytest.raises(CorpusCountError, match="strictly ordered"):
        count_scan(registry, corpus_snapshot())


def test_count_scan_rejects_duplicate_registry_unit_key() -> None:
    registry = (
        RegistryEntry("art::creative-work", "art", "word"),
        RegistryEntry("art::creative-work", "art", "word"),
    )

    with pytest.raises(CorpusCountError, match="unique"):
        count_scan(registry, corpus_snapshot())


@pytest.mark.parametrize(
    "entry",
    [
        RegistryEntry("INVALID", "art", "word"),
        RegistryEntry("art::creative-work", " ", "word"),
        RegistryEntry("art::creative-work", "art", "phrase"),
        RegistryEntry("art::creative-work", "two tokens", "word"),
    ],
)
def test_count_scan_rejects_malformed_registry_entry(entry: RegistryEntry) -> None:
    with pytest.raises(CorpusCountError):
        count_scan((entry,), corpus_snapshot())


def test_count_scan_rejects_non_registry_entry() -> None:
    with pytest.raises(CorpusCountError, match="RegistryEntry"):
        count_scan(("not an entry",), corpus_snapshot())  # type: ignore[arg-type]


def test_count_scan_rejects_unsorted_file_snapshots() -> None:
    corpus = corpus_snapshot(
        file_snapshot("b.txt", "art"),
        file_snapshot("a.txt", "art"),
    )

    with pytest.raises(CorpusCountError, match="strictly ordered"):
        count_scan((), corpus)


def test_count_scan_rejects_duplicate_file_paths() -> None:
    corpus = corpus_snapshot(
        file_snapshot("a.txt", "art", digest_seed="one"),
        file_snapshot("a.txt", "art", digest_seed="two"),
    )

    with pytest.raises(CorpusCountError, match="unique"):
        count_scan((), corpus)


@pytest.mark.parametrize("bad_sha", ["0" * 63, "A" * 64, "g" * 64, 1])
def test_count_scan_rejects_malformed_file_sha256(bad_sha: object) -> None:
    file = replace(file_snapshot("a.txt", "art"), sha256=bad_sha)  # type: ignore[arg-type]
    corpus = corpus_snapshot(file)

    with pytest.raises(CorpusCountError, match="invalid SHA-256"):
        count_scan((), corpus)


@pytest.mark.parametrize("blocks", [["art"], ("art", 1)])
def test_count_scan_rejects_malformed_blocks(blocks: object) -> None:
    file = replace(file_snapshot("a.txt", "art"), blocks=blocks)  # type: ignore[arg-type]
    corpus = corpus_snapshot(file)

    with pytest.raises(CorpusCountError, match="tuple of strings"):
        count_scan((), corpus)


@pytest.mark.parametrize("path", ["", ".", "..", "nested/a.txt", "bad.md"])
def test_count_scan_rejects_noncanonical_file_path(path: str) -> None:
    corpus = corpus_snapshot(file_snapshot(path, "art"))

    with pytest.raises(CorpusCountError, match="canonical filename"):
        count_scan((), corpus)


@pytest.mark.parametrize(
    ("source", "month"),
    [("Reading", "2026-08"), ("reading", "2026-13")],
)
def test_count_scan_rejects_invalid_source_or_month(source: str, month: str) -> None:
    corpus = corpus_snapshot(source=source, month=month)

    with pytest.raises(CorpusCountError, match="source or month"):
        count_scan((), corpus)


def test_count_scan_rejects_manually_altered_corpus_digest() -> None:
    corpus = replace(
        corpus_snapshot(file_snapshot("a.txt", "art")),
        digest="0" * 64,
    )

    with pytest.raises(CorpusCountError, match="does not match"):
        count_scan((), corpus)


@pytest.mark.parametrize("digest", ["0" * 63, "A" * 64, "g" * 64, 1])
def test_count_scan_rejects_malformed_corpus_digest(digest: object) -> None:
    corpus = replace(corpus_snapshot(), digest=digest)  # type: ignore[arg-type]

    with pytest.raises(CorpusCountError, match="invalid SHA-256"):
        count_scan((), corpus)


def test_encounter_id_matches_frozen_known_vector() -> None:
    result = scan_result(
        CorpusCount("art::creative-work", "art", "word", 3)
    )

    plan = build_encounter_plans(result)[0]

    assert plan.payload["encounter_id"] == (
        "5fcb9721e6cd7c3c75aea0b80bb7e345"
        "90356ac2ef2a4ea7923978ee8e6f2bb2"
    )


def test_encounter_id_is_deterministic_across_repeated_plan_builds() -> None:
    result = scan_result(word_count("art", 3))

    assert build_encounter_plans(result) == build_encounter_plans(result)


@pytest.mark.parametrize(
    "changed_result",
    [
        scan_result(word_count("art", 99)),
        scan_result(CorpusCount("art::sense", "music", "word", 1)),
        scan_result(
            CorpusCount("art::sense", "modern art", "chunk", 1)
        ),
        scan_result(
            word_count("art"),
            corpus_snapshot_digest=sha256(b"another corpus").hexdigest(),
        ),
        scan_result(word_count("art"), corpus_file_count=99),
    ],
)
def test_observation_provenance_does_not_affect_encounter_id(
    changed_result: CorpusScanResult,
) -> None:
    baseline = build_encounter_plans(scan_result(word_count("art")))[0]
    changed = build_encounter_plans(changed_result)[0]

    assert changed.payload["encounter_id"] == baseline.payload["encounter_id"]


@pytest.mark.parametrize(
    "changed_result",
    [
        scan_result(word_count("music")),
        scan_result(word_count("art"), source="own-writing"),
        scan_result(word_count("art"), month="2026-07"),
    ],
)
def test_encounter_identity_fields_change_encounter_id(
    changed_result: CorpusScanResult,
) -> None:
    baseline = build_encounter_plans(scan_result(word_count("art")))[0]
    changed = build_encounter_plans(changed_result)[0]

    assert changed.payload["encounter_id"] != baseline.payload["encounter_id"]


def test_build_encounter_plans_preserves_order_and_exact_payload() -> None:
    result = scan_result(
        word_count("art", 0),
        CorpusCount(
            "pose-a-threat-to::sense",
            "pose a threat to",
            "chunk",
            4,
        ),
        corpus_file_count=7,
    )

    plans = build_encounter_plans(result)

    assert tuple(plan.unit_key for plan in plans) == (
        "art::sense",
        "pose-a-threat-to::sense",
    )
    assert tuple(plans[0].payload) == T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS
    assert set(plans[0].payload) == set(T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS)
    assert plans[0].payload == {
        "count": 0,
        "source": result.source,
        "month": result.month,
        "producer": T10_ENCOUNTER_PRODUCER_ID,
        "scan_version": CORPUS_SCAN_VERSION,
        "encounter_id": expected_encounter_id("art::sense"),
        "lemma": "art",
        "unit_type": "word",
        "corpus_snapshot_digest": result.corpus_snapshot_digest,
        "corpus_file_count": 7,
    }


def test_empty_scan_result_builds_no_plans() -> None:
    assert build_encounter_plans(scan_result()) == ()


def test_encounter_plan_defensively_copies_and_freezes_payload() -> None:
    original = {"count": 1}
    plan = EncounterPlan("art::sense", original)

    original["count"] = 2

    assert plan.payload == {"count": 1}
    with pytest.raises(TypeError, match="immutable"):
        plan.payload["count"] = 3


@pytest.mark.parametrize("bad_count", [True, -1])
def test_build_encounter_plans_rejects_invalid_count(bad_count: object) -> None:
    result = scan_result(
        CorpusCount("art::sense", "art", "word", bad_count)  # type: ignore[arg-type]
    )

    with pytest.raises(CorpusEncounterError, match="non-negative integer"):
        build_encounter_plans(result)


@pytest.mark.parametrize("bad_file_count", [True, -1])
def test_build_encounter_plans_rejects_invalid_file_count(
    bad_file_count: object,
) -> None:
    result = scan_result(
        word_count("art"),
        corpus_file_count=bad_file_count,  # type: ignore[arg-type]
    )

    with pytest.raises(CorpusEncounterError, match="corpus_file_count"):
        build_encounter_plans(result)


@pytest.mark.parametrize("digest", ["0" * 63, "A" * 64, "g" * 64])
def test_build_encounter_plans_rejects_malformed_digest(digest: str) -> None:
    with pytest.raises(CorpusEncounterError, match="digest"):
        build_encounter_plans(
            scan_result(word_count("art"), corpus_snapshot_digest=digest)
        )


@pytest.mark.parametrize(
    ("source", "month"),
    [("Reading", "2026-08"), ("reading", "2026-13")],
)
def test_build_encounter_plans_rejects_invalid_source_or_month(
    source: str,
    month: str,
) -> None:
    with pytest.raises(CorpusEncounterError):
        build_encounter_plans(
            scan_result(word_count("art"), source=source, month=month)
        )


def test_build_encounter_plans_rejects_duplicate_unit_key() -> None:
    result = scan_result(word_count("art"), word_count("art", 2))

    with pytest.raises(CorpusEncounterError, match="unique"):
        build_encounter_plans(result)


def test_build_encounter_plans_rejects_unsorted_counts() -> None:
    result = scan_result(word_count("zero"), word_count("art"))

    with pytest.raises(CorpusEncounterError, match="strictly ordered"):
        build_encounter_plans(result)


@pytest.mark.parametrize(
    "count",
    [
        CorpusCount("INVALID", "art", "word", 1),
        CorpusCount("art::sense", " ", "word", 1),
        CorpusCount("art::sense", "art", "phrase", 1),
        CorpusCount("art::sense", "two tokens", "word", 1),
    ],
)
def test_build_encounter_plans_rejects_malformed_lexical_count(
    count: CorpusCount,
) -> None:
    with pytest.raises(CorpusEncounterError):
        build_encounter_plans(scan_result(count))


def test_build_encounter_plans_requires_exact_result_and_count_types() -> None:
    with pytest.raises(CorpusEncounterError, match="exact CorpusScanResult"):
        build_encounter_plans(object())  # type: ignore[arg-type]

    result = replace(scan_result(), counts=("bad",))  # type: ignore[arg-type]
    with pytest.raises(CorpusEncounterError, match="exact CorpusCount"):
        build_encounter_plans(result)


def test_non_t10_history_is_ignored_by_empty_scan_preflight() -> None:
    generic = Event(
        v=EVENT_SCHEMA_VERSION,
        ts="2026-08-23T00:00:00+00:00",
        day="2026-08-23",
        event="ENCOUNTER",
        unit_key="art::sense",
        payload={"count": 1, "source": "reading", "month": "2026-08"},
    )
    other_producer = replace(
        generic,
        payload={"producer": "other", "anything": object()},
    )
    state_with_t10_marker = replace(
        generic,
        event="STATE",
        payload={"producer": T10_ENCOUNTER_PRODUCER_ID},
    )
    event_log = FakeEventLog([generic, other_producer, state_with_t10_marker])

    report = emit_scan(scan_result(), event_log=event_log)  # type: ignore[arg-type]

    assert report == CorpusEmitReport("reading", "2026-08", (), ())
    assert event_log.read_calls == 1
    assert event_log.log_calls == []


@pytest.mark.parametrize(
    "malformation",
    [
        "missing_field",
        "extra_field",
        "scan_version_bool",
        "wrong_scan_version",
        "count_bool",
        "count_negative",
        "file_count_bool",
        "file_count_negative",
        "malformed_encounter_id",
        "mismatched_encounter_id",
        "malformed_corpus_digest",
        "blank_lemma",
        "invalid_unit_type",
        "invalid_unit_shape",
    ],
)
def test_historical_t10_payload_malformation_fails_closed(
    malformation: str,
) -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    payload = dict(plan.payload)
    if malformation == "missing_field":
        del payload["count"]
    elif malformation == "extra_field":
        payload["extra"] = "bad"
    elif malformation == "scan_version_bool":
        payload["scan_version"] = True
    elif malformation == "wrong_scan_version":
        payload["scan_version"] = CORPUS_SCAN_VERSION + 1
    elif malformation == "count_bool":
        payload["count"] = True
    elif malformation == "count_negative":
        payload["count"] = -1
    elif malformation == "file_count_bool":
        payload["corpus_file_count"] = True
    elif malformation == "file_count_negative":
        payload["corpus_file_count"] = -1
    elif malformation == "malformed_encounter_id":
        payload["encounter_id"] = "bad"
    elif malformation == "mismatched_encounter_id":
        payload["encounter_id"] = "0" * 64
    elif malformation == "malformed_corpus_digest":
        payload["corpus_snapshot_digest"] = "bad"
    elif malformation == "blank_lemma":
        payload["lemma"] = " "
    elif malformation == "invalid_unit_type":
        payload["unit_type"] = "phrase"
    elif malformation == "invalid_unit_shape":
        payload["lemma"] = "two tokens"
    event = event_from_plan(plan, event_updates={"payload": payload})
    event_log = FakeEventLog([event])

    with pytest.raises(CorpusHistoryError):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.read_calls == 1
    assert event_log.log_calls == []


def test_wrong_producer_encounter_is_outside_t10_namespace() -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    event = event_from_plan(
        plan,
        payload_updates={"producer": "other", "count": True},
    )
    event_log = FakeEventLog([event])

    report = emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert len(report.appended_encounter_ids) == 1
    assert report.existing_encounter_ids == ()


def test_historical_t10_event_rejects_malformed_unit_key() -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    event = event_from_plan(plan, event_updates={"unit_key": "INVALID"})
    event_log = FakeEventLog([event])

    with pytest.raises(CorpusHistoryError, match="unit_key"):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


@pytest.mark.parametrize("version", [True, 2])
def test_historical_t10_event_rejects_wrong_schema_version(version: object) -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    event = event_from_plan(plan, event_updates={"v": version})
    event_log = FakeEventLog([event])

    with pytest.raises(CorpusHistoryError, match="schema version"):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_duplicate_historical_t10_encounter_id_fails_even_when_identical() -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    event = event_from_plan(plan)
    event_log = FakeEventLog([event, event])

    with pytest.raises(CorpusHistoryError, match="globally unique"):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_emit_scan_with_no_history_appends_all_plans_once() -> None:
    result = scan_result(word_count("art"), word_count("music"), word_count("zero", 0))
    plans = build_encounter_plans(result)
    event_log = FakeEventLog()

    report = emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    expected_ids = tuple(plan.payload["encounter_id"] for plan in plans)
    assert report == CorpusEmitReport(
        source="reading",
        month="2026-08",
        appended_encounter_ids=expected_ids,
        existing_encounter_ids=(),
    )
    assert event_log.read_calls == 1
    assert [call[0] for call in event_log.log_calls] == ["ENCOUNTER"] * 3
    assert [call[1] for call in event_log.log_calls] == [
        "art::sense",
        "music::sense",
        "zero::sense",
    ]
    assert all(
        tuple(payload) == T10_ENCOUNTER_ALLOWED_PAYLOAD_FIELDS
        for _event, _unit_key, payload in event_log.log_calls
    )


def test_exact_rerun_appends_zero_and_reports_all_existing() -> None:
    result = scan_result(word_count("art"), word_count("music"))
    event_log = FakeEventLog()
    first = emit_scan(result, event_log=event_log)  # type: ignore[arg-type]
    first_call_count = len(event_log.log_calls)

    second = emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert first.appended_encounter_ids == second.existing_encounter_ids
    assert second.appended_encounter_ids == ()
    assert len(event_log.log_calls) == first_call_count
    assert event_log.read_calls == 2


def test_partial_history_skips_existing_and_appends_only_missing() -> None:
    result = scan_result(word_count("art"), word_count("music"), word_count("zero"))
    plans = build_encounter_plans(result)
    event_log = FakeEventLog(
        [event_from_plan(plans[2]), event_from_plan(plans[0])]
    )

    report = emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert report.existing_encounter_ids == (
        plans[0].payload["encounter_id"],
        plans[2].payload["encounter_id"],
    )
    assert report.appended_encounter_ids == (plans[1].payload["encounter_id"],)
    assert [call[1] for call in event_log.log_calls] == ["music::sense"]


@pytest.mark.parametrize(
    "payload_updates",
    [
        {"count": 99},
        {"lemma": "music"},
        {"unit_type": "chunk"},
    ],
)
def test_same_encounter_id_with_semantic_difference_fails_before_append(
    payload_updates: dict[str, object],
) -> None:
    result = scan_result(word_count("art"))
    plan = build_encounter_plans(result)[0]
    event_log = FakeEventLog([event_from_plan(plan, payload_updates=payload_updates)])

    with pytest.raises(CorpusHistoryError):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_current_source_month_corpus_digest_is_immutable() -> None:
    old_result = scan_result(
        word_count("art"),
        corpus_snapshot_digest=sha256(b"old").hexdigest(),
    )
    current_result = replace(
        old_result,
        corpus_snapshot_digest=sha256(b"new").hexdigest(),
    )
    historical = event_from_plan(build_encounter_plans(old_result)[0])
    event_log = FakeEventLog([historical])

    with pytest.raises(CorpusHistoryError, match="digest is immutable"):
        emit_scan(current_result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_same_digest_with_different_corpus_file_count_is_immutable() -> None:
    old_result = scan_result(word_count("art"), corpus_file_count=2)
    current_result = replace(old_result, corpus_file_count=3)
    historical = event_from_plan(build_encounter_plans(old_result)[0])
    event_log = FakeEventLog([historical])

    with pytest.raises(CorpusHistoryError, match="file count is immutable"):
        emit_scan(current_result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_unrelated_history_rejects_same_artifact_with_different_file_counts() -> None:
    digest = sha256(b"same artifact").hexdigest()
    first_result = scan_result(
        word_count("art"),
        source="other",
        corpus_snapshot_digest=digest,
        corpus_file_count=2,
    )
    second_result = scan_result(
        word_count("music"),
        source="other",
        corpus_snapshot_digest=digest,
        corpus_file_count=3,
    )
    history = [
        event_from_plan(build_encounter_plans(first_result)[0]),
        event_from_plan(build_encounter_plans(second_result)[0]),
    ]
    event_log = FakeEventLog(history)

    with pytest.raises(CorpusHistoryError, match="one artifact"):
        emit_scan(scan_result(), event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_removed_unit_history_still_enforces_corpus_immutability() -> None:
    old_result = scan_result(
        word_count("removed"),
        corpus_snapshot_digest=sha256(b"old").hexdigest(),
    )
    current_result = scan_result(
        corpus_snapshot_digest=sha256(b"new").hexdigest(),
    )
    historical = event_from_plan(build_encounter_plans(old_result)[0])
    event_log = FakeEventLog([historical])

    with pytest.raises(CorpusHistoryError, match="digest is immutable"):
        emit_scan(current_result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_empty_current_plans_still_preflight_valid_matching_history() -> None:
    old_result = scan_result(word_count("removed"))
    current_result = scan_result()
    event_log = FakeEventLog(
        [event_from_plan(build_encounter_plans(old_result)[0])]
    )

    report = emit_scan(current_result, event_log=event_log)  # type: ignore[arg-type]

    assert report == CorpusEmitReport("reading", "2026-08", (), ())
    assert event_log.read_calls == 1
    assert event_log.log_calls == []


def test_malformed_later_history_causes_zero_appends() -> None:
    result = scan_result(word_count("art"))
    unrelated_plan = build_encounter_plans(
        scan_result(word_count("music"), source="other")
    )[0]
    malformed = event_from_plan(
        unrelated_plan,
        payload_updates={"count": True},
    )
    event_log = FakeEventLog(
        [
            Event(
                v=EVENT_SCHEMA_VERSION,
                ts="2026-08-23T00:00:00+00:00",
                day="2026-08-23",
                event="FORGE",
                unit_key="art::sense",
                payload={},
            ),
            malformed,
        ]
    )

    with pytest.raises(CorpusHistoryError):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_later_plan_conflict_causes_zero_appends_for_all_plans() -> None:
    result = scan_result(word_count("art"), word_count("music"))
    plans = build_encounter_plans(result)
    conflict = event_from_plan(plans[1], payload_updates={"count": 99})
    event_log = FakeEventLog([conflict])

    with pytest.raises(CorpusHistoryError, match="conflicts"):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_partial_append_failure_resumes_only_missing_events() -> None:
    result = scan_result(
        word_count("alpha"),
        word_count("bravo"),
        word_count("charlie"),
        word_count("delta"),
        word_count("echo"),
    )
    plans = build_encounter_plans(result)
    failing_log = FakeEventLog(fail_on_append=3)

    with pytest.raises(CorpusEmissionError) as raised:
        emit_scan(result, event_log=failing_log)  # type: ignore[arg-type]

    assert isinstance(raised.value.__cause__, OSError)
    assert len(failing_log.events) == 2
    assert [call[1] for call in failing_log.log_calls] == [
        "alpha::sense",
        "bravo::sense",
        "charlie::sense",
    ]

    resumed_log = FakeEventLog(failing_log.events)
    report = emit_scan(result, event_log=resumed_log)  # type: ignore[arg-type]

    assert report.existing_encounter_ids == tuple(
        plan.payload["encounter_id"] for plan in plans[:2]
    )
    assert report.appended_encounter_ids == tuple(
        plan.payload["encounter_id"] for plan in plans[2:]
    )
    assert [call[1] for call in resumed_log.log_calls] == [
        "charlie::sense",
        "delta::sense",
        "echo::sense",
    ]
    final_ids = [event.payload["encounter_id"] for event in resumed_log.events]
    assert len(final_ids) == len(set(final_ids)) == 5


@pytest.mark.parametrize(
    "return_transform",
    [
        lambda _stored: object(),
        lambda stored: replace(stored, event="STATE"),
        lambda stored: replace(stored, unit_key="other::sense"),
        lambda stored: replace(stored, payload={**stored.payload, "count": 99}),
        lambda stored: replace(stored, v=EVENT_SCHEMA_VERSION + 1),
    ],
)
def test_emit_scan_rejects_untrusted_log_return_and_stops_immediately(
    return_transform: Callable[[Event], object],
) -> None:
    result = scan_result(word_count("art"), word_count("music"))
    event_log = FakeEventLog(return_transform=return_transform)

    with pytest.raises(CorpusEmissionError):
        emit_scan(result, event_log=event_log)  # type: ignore[arg-type]

    assert len(event_log.log_calls) == 1


def test_event_log_read_failure_is_wrapped_with_cause_and_never_appends() -> None:
    failure = OSError("read failed")
    event_log = FakeEventLog(read_error=failure)

    with pytest.raises(CorpusHistoryError) as raised:
        emit_scan(scan_result(word_count("art")), event_log=event_log)  # type: ignore[arg-type]

    assert raised.value.__cause__ is failure
    assert event_log.read_calls == 1
    assert event_log.log_calls == []


def test_event_log_append_failure_is_wrapped_with_cause_and_stops() -> None:
    event_log = FakeEventLog(fail_on_append=1)

    with pytest.raises(CorpusEmissionError) as raised:
        emit_scan(
            scan_result(word_count("art"), word_count("music")),
            event_log=event_log,  # type: ignore[arg-type]
        )

    assert isinstance(raised.value.__cause__, OSError)
    assert len(event_log.log_calls) == 1
    assert event_log.events == []


@pytest.mark.parametrize("read_result", [(), {}, "bad"])
def test_event_log_read_must_return_a_list(read_result: object) -> None:
    event_log = FakeEventLog(read_result=read_result)

    with pytest.raises(CorpusHistoryError, match="must be a list"):
        emit_scan(scan_result(), event_log=event_log)  # type: ignore[arg-type]

    assert event_log.read_calls == 1
    assert event_log.log_calls == []


def test_event_log_read_list_must_contain_only_events() -> None:
    event_log = FakeEventLog(read_result=[object()])

    with pytest.raises(CorpusHistoryError, match="Event values only"):
        emit_scan(scan_result(), event_log=event_log)  # type: ignore[arg-type]

    assert event_log.log_calls == []


def test_t10_emission_architecture_is_injected_encounter_only() -> None:
    module_source = inspect.getsource(corpus_module)
    emit_source = inspect.getsource(emit_scan)
    preflight_source = inspect.getsource(corpus_module._preflight_emission)
    tree = ast.parse(module_source)
    log_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "log"
    ]

    assert len(log_calls) == 1
    assert isinstance(log_calls[0].args[0], ast.Constant)
    assert log_calls[0].args[0].value == "ENCOUNTER"
    assert preflight_source.count("event_log.read()") == 1
    assert "event_log.log" not in preflight_source
    assert "read_registry_snapshot" not in emit_source
    assert "read_corpus_snapshot" not in emit_source
    assert "count_scan" not in emit_source
    assert "STATE" not in emit_source
    assert "JUDGE" not in emit_source
    assert "PREPARE" not in module_source
    assert "COMMIT" not in module_source
    assert "sqlite" not in module_source.casefold()
    assert "reconcile" not in module_source.casefold()
