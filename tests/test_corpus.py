from __future__ import annotations

import ast
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

import vocab.corpus as corpus_module
from vocab.contracts import ANKI_NOTE_TYPE_NAME, CORPUS_SCAN_VERSION, NOTE_FIELDS
from vocab.corpus import (
    CorpusCount,
    CorpusCountError,
    CorpusFileSnapshot,
    CorpusRegistryError,
    CorpusScanResult,
    CorpusSnapshot,
    CorpusSnapshotError,
    RegistryEntry,
    count_scan,
    count_unit_occurrences,
    read_corpus_snapshot,
    read_registry_snapshot,
)


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
        {"random", "time", "datetime", "requests", "urllib"}
    )
    for forbidden in (
        "EventLog",
        "event_log.log",
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
