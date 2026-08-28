"""D70 Wave A runtime foundation tests.

Every refusal test asserts the absence of a forbidden side effect as well as
the refusal itself, because a command that raises after touching the disk is
still a fail-open command.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from vocab import cli
from vocab.anki import AnkiConnectError
from vocab.events import EventLog
from vocab.artifact_json import canonical_sha256
from vocab.contracts import NOTE_FIELDS
from vocab.runtime import bootstrap as bootstrap_module
from vocab.runtime.bootstrap import (
    BootstrapPreconditions,
    create_deployment,
    evaluate_preconditions,
)
from vocab.runtime.config import (
    ANKI_CONFIG_KEYS,
    CONFIG_KEYS,
    RuntimeConfig,
    load_config,
    validated_deployment_path,
)
from vocab.runtime.errors import (
    RuntimeBootstrapError,
    RuntimeConfigError,
    RuntimeEventLogError,
    RuntimeIdentityError,
    RuntimeLockError,
)
from vocab.runtime.eventlog_authority import open_runtime_event_log
from vocab.runtime.identity import (
    IDENTITY_KEYS,
    RuntimeIdentity,
    publish_identity,
    read_identity,
    registry_digest,
    validated_identity_mapping,
)
from vocab.runtime.layout import (
    DURABLE_LAYOUT_ENTRY_NAMES,
    LOCK_FILE_NAME,
    build_layout,
    missing_durable_entries,
)
from vocab.runtime.lock import DeploymentLock, read_lock_state
from vocab.runtime.preflight import (
    FAIL,
    NOT_EVALUATED,
    PASS,
    run_bootstrap_preflight,
    run_runtime_write_preflight,
    run_standalone_preflight,
)


# ----------------------------------------------------------------------
# Oracles and fixtures
# ----------------------------------------------------------------------


def snapshot(root: Path) -> dict[str, object]:
    """Recursive (path, kind, size, digest) snapshot excluding the lock.

    The lock is excluded because it legitimately appears and disappears. Every
    other difference is a side effect.
    """
    if not root.exists():
        return {}
    result: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == LOCK_FILE_NAME:
            continue
        if path.is_dir():
            result[relative] = "dir"
        else:
            payload = path.read_bytes()
            result[relative] = (len(payload), hashlib.sha256(payload).hexdigest())
    return result


def make_note(note_id: int, unit_key: str, lemma: str) -> dict[str, object]:
    fields = {name: {"value": "", "order": index} for index, name in enumerate(NOTE_FIELDS)}
    fields["unit_key"]["value"] = unit_key
    fields["lemma"]["value"] = lemma
    fields["unit_type"]["value"] = "word"
    return {"noteId": note_id, "modelName": "VocabularyUnit", "fields": fields}


class FakeAnki:
    """A deterministic AnkiConnect stand-in for preflight and enumeration."""

    def __init__(self, notes: list[dict[str, object]] | None = None) -> None:
        self.notes = list(notes or [])
        self.note_type_ok = True
        self.deck_ok = True
        self.leech_ok = True
        self.calls: list[str] = []

    def verify_note_type(self) -> bool:
        self.calls.append("verify_note_type")
        if not self.note_type_ok:
            raise AnkiConnectError("note type mismatch")
        return True

    def get_deck_config(self, deck_name: str) -> dict[str, object]:
        self.calls.append("get_deck_config")
        if not self.deck_ok:
            raise AnkiConnectError("deck missing")
        return {"name": deck_name}

    def verify_leech_config(self, deck_name: str) -> bool:
        self.calls.append("verify_leech_config")
        if not self.leech_ok:
            raise AnkiConnectError("leech config mismatch")
        return True

    def find_notes(self, query: str) -> list[int]:
        self.calls.append("find_notes")
        return [int(note["noteId"]) for note in self.notes]

    def notes_info(self, note_ids: object) -> list[dict[str, object]]:
        self.calls.append("notes_info")
        wanted = list(note_ids)  # type: ignore[arg-type]
        by_id = {int(note["noteId"]): note for note in self.notes}
        return [by_id[note_id] for note_id in wanted]


def config_mapping(tmp_path: Path) -> dict[str, object]:
    return {
        "config_version": 1,
        "data_root": str(tmp_path / "prod"),
        "corpus_root": str(tmp_path / "corpus"),
        "anki": {
            "endpoint": "http://127.0.0.1:8765",
            "timeout": 10.0,
            "deck_name": "Vocabulary",
        },
    }


def write_config(tmp_path: Path, mapping: dict[str, object] | None = None) -> Path:
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(mapping if mapping is not None else config_mapping(tmp_path)),
        encoding="utf-8",
    )
    return path


def bootstrap_deployment(tmp_path: Path, anki: FakeAnki | None = None) -> RuntimeConfig:
    config = load_config(write_config(tmp_path))
    client = anki or FakeAnki([make_note(1, "alpha::demo", "alpha")])
    preconditions = evaluate_preconditions(
        config,
        client,
        confirm_new_deployment=True,
        confirm_clean_production_profile=True,
    )
    create_deployment(preconditions)
    return config


# ----------------------------------------------------------------------
# G-9 / G-16: configuration
# ----------------------------------------------------------------------


def test_config_round_trips_and_keysets_are_closed() -> None:
    assert CONFIG_KEYS == {"config_version", "data_root", "corpus_root", "anki"}
    assert ANKI_CONFIG_KEYS == {"endpoint", "timeout", "deck_name"}


def test_config_accepts_a_valid_file(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    assert config.config_version == 1
    assert config.anki.deck_name == "Vocabulary"


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(lambda m: m.update(extra=1), id="unknown-top-level-key"),
        pytest.param(lambda m: m.pop("corpus_root"), id="missing-key"),
        pytest.param(lambda m: m.update(config_version=2), id="wrong-version"),
        pytest.param(lambda m: m.update(config_version=True), id="bool-version"),
        pytest.param(lambda m: m.update(data_root="relative/path"), id="relative-path"),
        pytest.param(lambda m: m.update(data_root=""), id="empty-path"),
        pytest.param(lambda m: m["anki"].update(extra=1), id="unknown-anki-key"),
        pytest.param(lambda m: m["anki"].pop("timeout"), id="missing-anki-key"),
        pytest.param(lambda m: m["anki"].update(timeout=0), id="zero-timeout"),
        pytest.param(lambda m: m["anki"].update(timeout=-1), id="negative-timeout"),
        pytest.param(lambda m: m["anki"].update(timeout=True), id="bool-timeout"),
        pytest.param(lambda m: m["anki"].update(deck_name=" x "), id="padded-deck"),
        pytest.param(lambda m: m["anki"].update(deck_name=""), id="empty-deck"),
        pytest.param(lambda m: m["anki"].update(endpoint=""), id="empty-endpoint"),
        pytest.param(lambda m: m.update(anki=[]), id="anki-not-object"),
    ),
)
def test_config_rejects_every_schema_violation(tmp_path: Path, mutate) -> None:
    mapping = config_mapping(tmp_path)
    mutate(mapping)
    path = write_config(tmp_path, mapping)
    before = snapshot(tmp_path)
    with pytest.raises(RuntimeConfigError):
        load_config(path)
    assert snapshot(tmp_path) == before


def test_config_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text('{"config_version": 1, "config_version": 1}', encoding="utf-8")
    with pytest.raises(RuntimeConfigError):
        load_config(path)


def test_deployment_path_rejects_dot_components(tmp_path: Path) -> None:
    absolute = tmp_path.as_posix()
    with pytest.raises(RuntimeConfigError):
        validated_deployment_path(f"{absolute}/../x", "data_root")
    with pytest.raises(RuntimeConfigError):
        validated_deployment_path(f"{absolute}/./x", "data_root")


# ----------------------------------------------------------------------
# G-10: identity schema and D-1..D-6
# ----------------------------------------------------------------------


def valid_identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        identity_version=1,
        runtime_id="0d3f1a2b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
        layout_version=1,
        created_utc="2026-08-27T00:00:00+00:00",
        bootstrap_registry_count=0,
        bootstrap_registry_digest=f"sha256:{canonical_sha256([])}",
    )


def identity_body() -> dict[str, object]:
    identity = valid_identity()
    return {
        "identity_version": identity.identity_version,
        "runtime_id": identity.runtime_id,
        "layout_version": identity.layout_version,
        "created_utc": identity.created_utc,
        "bootstrap_registry_count": identity.bootstrap_registry_count,
        "bootstrap_registry_digest": identity.bootstrap_registry_digest,
    }


def test_identity_keyset_is_exact() -> None:
    assert IDENTITY_KEYS == set(identity_body())


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(lambda b: b.update(extra=1), id="unknown-key"),
        pytest.param(lambda b: b.pop("runtime_id"), id="missing-key"),
        pytest.param(lambda b: b.update(identity_version=2), id="wrong-identity-version"),
        pytest.param(lambda b: b.update(layout_version=2), id="wrong-layout-version"),
        pytest.param(lambda b: b.update(runtime_id="not-a-uuid"), id="bad-uuid"),
        pytest.param(
            lambda b: b.update(runtime_id="0d3f1a2b-4c5d-3e6f-8a9b-0c1d2e3f4a5b"),
            id="uuid-wrong-version-nibble",
        ),
        pytest.param(lambda b: b.update(created_utc="2026-08-27T00:00:00Z"), id="unnormalized-utc"),
        pytest.param(lambda b: b.update(created_utc="2026-08-27T00:00:00"), id="naive-utc"),
        pytest.param(lambda b: b.update(bootstrap_registry_count=-1), id="negative-count"),
        pytest.param(lambda b: b.update(bootstrap_registry_count=True), id="bool-count"),
        pytest.param(lambda b: b.update(bootstrap_registry_digest="deadbeef"), id="no-prefix"),
        pytest.param(
            lambda b: b.update(bootstrap_registry_digest="sha256:" + "A" * 64),
            id="uppercase-digest",
        ),
        pytest.param(
            lambda b: b.update(bootstrap_registry_digest="sha256:" + "a" * 63),
            id="short-digest",
        ),
    ),
)
def test_identity_rejects_every_schema_violation(mutate) -> None:
    body = identity_body()
    mutate(body)
    with pytest.raises(RuntimeIdentityError):
        validated_identity_mapping(body)


def test_identity_rejects_non_object() -> None:
    with pytest.raises(RuntimeIdentityError):
        validated_identity_mapping([])


def test_identity_publication_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "runtime-identity.json"
    publish_identity(path, valid_identity())
    assert read_identity(path) == valid_identity()


def test_identity_publication_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "runtime-identity.json"
    publish_identity(path, valid_identity())
    before = path.read_bytes()
    with pytest.raises(RuntimeIdentityError):
        publish_identity(path, valid_identity())
    assert path.read_bytes() == before


def test_identity_publication_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime-identity.json"
    publish_identity(path, valid_identity())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["runtime-identity.json"]


def test_missing_identity_is_not_a_committed_deployment(tmp_path: Path) -> None:
    with pytest.raises(RuntimeIdentityError):
        read_identity(tmp_path / "runtime-identity.json")


# ----------------------------------------------------------------------
# G-15: registry digest projection
# ----------------------------------------------------------------------


def test_registry_digest_is_the_frozen_unit_key_projection() -> None:
    keys = ["alpha::demo", "beta::demo"]
    assert registry_digest(keys) == f"sha256:{canonical_sha256(keys)}"


def test_registry_digest_requires_sorted_unique_projection() -> None:
    with pytest.raises(RuntimeIdentityError):
        registry_digest(["beta::demo", "alpha::demo"])
    with pytest.raises(RuntimeIdentityError):
        registry_digest(["alpha::demo", "alpha::demo"])


def test_registry_digest_ignores_note_ids(tmp_path: Path) -> None:
    """Renumbering notes must not move the digest; note_id is excluded."""
    first = FakeAnki([make_note(1, "alpha::demo", "alpha")])
    second = FakeAnki([make_note(9999, "alpha::demo", "alpha")])
    config = load_config(write_config(tmp_path))
    one = evaluate_preconditions(
        config, first, confirm_new_deployment=False, confirm_clean_production_profile=False
    )
    two = evaluate_preconditions(
        config, second, confirm_new_deployment=False, confirm_clean_production_profile=False
    )
    assert one.registry_digest == two.registry_digest


def test_empty_profile_is_not_an_error(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    result = evaluate_preconditions(
        config,
        FakeAnki([]),
        confirm_new_deployment=False,
        confirm_clean_production_profile=False,
    )
    assert result.registry_count == 0
    assert result.registry_digest == f"sha256:{canonical_sha256([])}"


def test_duplicate_unit_key_fails_closed(tmp_path: Path) -> None:
    anki = FakeAnki(
        [make_note(1, "alpha::demo", "alpha"), make_note(2, "alpha::demo", "alpha")]
    )
    config = load_config(write_config(tmp_path))
    before = snapshot(tmp_path)
    with pytest.raises(Exception):
        evaluate_preconditions(
            config,
            anki,
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert snapshot(tmp_path) == before


# ----------------------------------------------------------------------
# G-7: bootstrap phase 0 has no side effects
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("new_deployment", "clean_profile"),
    ((False, False), (True, False), (False, True)),
)
def test_missing_confirmation_creates_nothing(
    tmp_path: Path, new_deployment: bool, clean_profile: bool
) -> None:
    config = load_config(write_config(tmp_path))
    anki = FakeAnki([make_note(1, "alpha::demo", "alpha")])
    before = snapshot(tmp_path)
    preconditions = evaluate_preconditions(
        config,
        anki,
        confirm_new_deployment=new_deployment,
        confirm_clean_production_profile=clean_profile,
    )
    assert not preconditions.confirmations_present
    with pytest.raises(RuntimeBootstrapError):
        create_deployment(preconditions)
    assert snapshot(tmp_path) == before
    assert not config.data_root.exists()


def test_failed_bootstrap_preflight_creates_nothing(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    anki = FakeAnki([make_note(1, "alpha::demo", "alpha")])
    anki.note_type_ok = False
    before = snapshot(tmp_path)
    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            anki,
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert snapshot(tmp_path) == before
    assert not config.data_root.exists()


def test_nonempty_data_root_is_refused(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    (config.data_root / "stray.txt").write_text("keep me", encoding="utf-8")
    before = snapshot(config.data_root)
    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            FakeAnki([]),
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert snapshot(config.data_root) == before


def test_bootstrap_creates_every_durable_entry(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    assert missing_durable_entries(layout) == ()
    present = sorted(p.name for p in config.data_root.iterdir())
    assert present == sorted(DURABLE_LAYOUT_ENTRY_NAMES)
    assert len(DURABLE_LAYOUT_ENTRY_NAMES) == 8
    assert not layout.lock_path.exists()


def test_bootstrap_preflight_never_reads_identity(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    anki = FakeAnki([])
    report = run_bootstrap_preflight(config, anki)
    assert report.ok
    assert all(check.name.startswith("anki.") for check in report.checks)


def test_existing_journal_is_never_adopted(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    journal = config.data_root / "events.jsonl"
    journal.write_text('{"pre": "existing"}\n', encoding="utf-8")
    before = journal.read_bytes()
    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            FakeAnki([]),
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert journal.read_bytes() == before


# ----------------------------------------------------------------------
# G-8: interruption boundaries
# ----------------------------------------------------------------------


# I-1 leaves an empty directory, which carries no state to adopt, repair, or
# remove; it is covered separately below. I-2 leaves only the ephemeral lock.
BOUNDARY_ENTRIES = (
    "I-3-artifacts",
    "I-4-sessions",
    "I-5-events",
    "I-6-ledgers",
    "I-7-transcriptions",
)


def simulate_interruption(root: Path, boundary: str) -> None:
    """Materialise the observable state left by a crash at one boundary."""
    root.mkdir(parents=True, exist_ok=True)
    if boundary == "I-1-data-root":
        return
    (root / "artifacts").mkdir(exist_ok=True)
    if boundary == "I-3-artifacts":
        return
    (root / "sessions").mkdir(exist_ok=True)
    if boundary == "I-4-sessions":
        return
    (root / "events.jsonl").touch()
    if boundary == "I-5-events":
        return
    for name in ("t12-exposures.jsonl", "t12-captures.jsonl", "t12-dispositions.jsonl"):
        (root / name).touch()
    if boundary == "I-6-ledgers":
        return
    (root / "t12-transcriptions.jsonl").touch()


@pytest.mark.parametrize("boundary", ("I-1-data-root", *BOUNDARY_ENTRIES))
def test_interrupted_bootstrap_leaves_no_identity(tmp_path: Path, boundary: str) -> None:
    config = load_config(write_config(tmp_path))
    simulate_interruption(config.data_root, boundary)
    assert not (config.data_root / "runtime-identity.json").exists()


def test_empty_data_root_is_not_a_deployment_but_is_bootstrappable(
    tmp_path: Path,
) -> None:
    """I-1 leaves an empty directory: incomplete, yet nothing to adopt."""
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    assert cli.main(["preflight", "--config", str(tmp_path / "runtime.json")]) == (
        cli.EXIT_REFUSED
    )
    bootstrap_deployment(tmp_path)
    assert missing_durable_entries(build_layout(config.data_root)) == ()


@pytest.mark.parametrize("boundary", BOUNDARY_ENTRIES)
def test_interrupted_bootstrap_is_not_resumed_or_repaired(
    tmp_path: Path, boundary: str
) -> None:
    config = load_config(write_config(tmp_path))
    simulate_interruption(config.data_root, boundary)
    before = snapshot(config.data_root)

    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            FakeAnki([]),
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert snapshot(config.data_root) == before

    exit_code = cli.main(["preflight", "--config", str(tmp_path / "runtime.json")])
    assert exit_code == cli.EXIT_REFUSED
    assert snapshot(config.data_root) == before


@pytest.mark.parametrize(
    "subset",
    (
        ("t12-exposures.jsonl",),
        ("t12-exposures.jsonl", "t12-captures.jsonl"),
        ("t12-captures.jsonl", "t12-dispositions.jsonl"),
    ),
)
def test_partial_ledger_subsets_fail_closed(tmp_path: Path, subset: tuple[str, ...]) -> None:
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    for name in subset:
        (config.data_root / name).touch()
    before = snapshot(config.data_root)
    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            FakeAnki([]),
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert snapshot(config.data_root) == before


@pytest.mark.parametrize("entry", DURABLE_LAYOUT_ENTRY_NAMES)
def test_removing_any_durable_entry_fails_closed(tmp_path: Path, entry: str) -> None:
    config = bootstrap_deployment(tmp_path)
    target = config.data_root / entry
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    before = snapshot(config.data_root)
    exit_code = cli.main(["preflight", "--config", str(tmp_path / "runtime.json")])
    assert exit_code == cli.EXIT_REFUSED
    assert snapshot(config.data_root) == before


# ----------------------------------------------------------------------
# G-11: lock and construction
# ----------------------------------------------------------------------


def test_lock_is_exclusive_and_never_broken(tmp_path: Path) -> None:
    path = tmp_path / LOCK_FILE_NAME
    first = DeploymentLock(path)
    first.acquire()
    assert read_lock_state(path).held
    second = DeploymentLock(path)
    with pytest.raises(RuntimeLockError):
        second.acquire()
    assert path.exists()
    first.release()
    assert not path.exists()


def test_lock_records_pid_and_timestamp(tmp_path: Path) -> None:
    path = tmp_path / LOCK_FILE_NAME
    with DeploymentLock(path):
        state = read_lock_state(path)
        assert state.held and state.pid == os.getpid()
        assert state.acquired_utc is not None


def test_lock_is_released_when_the_operation_raises(tmp_path: Path) -> None:
    path = tmp_path / LOCK_FILE_NAME
    with pytest.raises(ValueError):
        with DeploymentLock(path):
            raise ValueError("operation failed")
    assert not path.exists()


def test_bootstrap_acquires_the_lock_before_creating_durable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The oracle is event ORDER, not co-occurrence.

    A test asserting only that the lock exists while durable state is created
    would still pass an implementation that created state first and locked
    afterwards. This records a sequence and asserts the index of the lock
    acquisition precedes the first durable creation.
    """
    events: list[str] = []
    real_acquire = DeploymentLock.acquire

    def traced_acquire(self: DeploymentLock) -> None:
        real_acquire(self)
        events.append("lock")

    real_initialize = bootstrap_module.initialize_t12_ledgers

    def traced_initialize(**kwargs: object) -> object:
        events.append("ledgers")
        return real_initialize(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(DeploymentLock, "acquire", traced_acquire)
    monkeypatch.setattr(bootstrap_module, "initialize_t12_ledgers", traced_initialize)
    bootstrap_deployment(tmp_path)
    assert events.index("lock") < events.index("ledgers")


def test_held_lock_refuses_bootstrap_without_breaking_it(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    lock_path = config.data_root / LOCK_FILE_NAME
    holder = DeploymentLock(lock_path)
    holder.acquire()
    contents = lock_path.read_bytes()
    preconditions = evaluate_preconditions(
        config,
        FakeAnki([]),
        confirm_new_deployment=True,
        confirm_clean_production_profile=True,
    )
    with pytest.raises(RuntimeLockError):
        create_deployment(preconditions)
    assert lock_path.read_bytes() == contents
    holder.release()


def test_standalone_preflight_reports_lock_state_without_touching_it(
    tmp_path: Path,
) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    holder = DeploymentLock(layout.lock_path)
    holder.acquire()
    contents = layout.lock_path.read_bytes()
    report = run_standalone_preflight(config, layout, FakeAnki([]))
    lock_check = next(check for check in report.checks if check.name == "lock.state")
    assert lock_check.detail.startswith("HELD")
    assert layout.lock_path.read_bytes() == contents
    holder.release()


def test_standalone_preflight_never_constructs_a_journal(tmp_path: Path) -> None:
    """This is the behavioural evidence for D70 section 7.4.

    The journal class opens in append mode. If the standalone preflight ever
    constructed one, a concurrently removed journal would be recreated empty
    and an empty history reads clean, so a lost production history would be
    reported healthy.
    """
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    layout.event_log_path.unlink()
    report = run_standalone_preflight(config, layout, FakeAnki([]))
    assert not layout.event_log_path.exists()
    history = next(
        check for check in report.checks if check.name == "eventlog.strict_history"
    )
    assert history.status == NOT_EVALUATED


def test_standalone_preflight_marks_ledger_checks_not_evaluated(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    report = run_standalone_preflight(config, layout, FakeAnki([]))
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["t12.triple_consistency"] == NOT_EVALUATED
    assert statuses["eventlog.strict_history"] == NOT_EVALUATED
    assert statuses["identity.committed"] == PASS


def test_runtime_write_preflight_evaluates_history(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    with DeploymentLock(layout.lock_path):
        report = run_runtime_write_preflight(config, layout, FakeAnki([]))
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["eventlog.strict_history"] == PASS
    assert statuses["t12.triple_consistency"] == PASS
    assert report.ok


# ----------------------------------------------------------------------
# G-4 / G-12: journal authority runtime behaviour
# ----------------------------------------------------------------------


def test_authority_requires_an_existing_absolute_regular_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(Path("events.jsonl"))
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(tmp_path / "absent.jsonl")
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(str(tmp_path / "absent.jsonl"))  # type: ignore[arg-type]
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(tmp_path)


def test_authority_never_creates_a_missing_journal(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(target)
    assert not target.exists()


def test_corrupt_journal_fails_closed_without_mutation(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    layout.event_log_path.write_text('{"broken": true}', encoding="utf-8")
    before = snapshot(config.data_root)
    with DeploymentLock(layout.lock_path):
        report = run_runtime_write_preflight(config, layout, FakeAnki([]))
    history = next(
        check for check in report.checks if check.name == "eventlog.strict_history"
    )
    assert history.status == FAIL
    assert snapshot(config.data_root) == before


# ----------------------------------------------------------------------
# G-16: CLI exit codes
# ----------------------------------------------------------------------


def test_cli_exit_codes_are_distinct() -> None:
    codes = {
        cli.EXIT_SUCCESS,
        cli.EXIT_REFUSED,
        cli.EXIT_USAGE,
        cli.EXIT_LOCK_CONTENTION,
        cli.EXIT_ITEM_FAILURES,
    }
    assert codes == {0, 1, 2, 3, 4}


def test_cli_requires_an_explicit_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["preflight"])
    assert excinfo.value.code == cli.EXIT_USAGE
    assert snapshot(tmp_path) == before


def test_cli_preflight_refuses_an_uncommitted_data_root(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    before = snapshot(tmp_path)
    assert cli.main(["preflight", "--config", str(tmp_path / "runtime.json")]) == (
        cli.EXIT_REFUSED
    )
    assert snapshot(tmp_path) == before
    assert not config.data_root.exists()


def test_cli_bootstrap_without_confirmation_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_build_anki", lambda config: FakeAnki([]))
    config = load_config(write_config(tmp_path))
    before = snapshot(tmp_path)
    exit_code = cli.main(["bootstrap", "--config", str(tmp_path / "runtime.json")])
    assert exit_code == cli.EXIT_REFUSED
    assert snapshot(tmp_path) == before
    assert not config.data_root.exists()


def test_cli_bootstrap_then_preflight_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "_build_anki", lambda config: FakeAnki([make_note(1, "alpha::demo", "alpha")])
    )
    config_path = str(tmp_path / "runtime.json")
    load_config(write_config(tmp_path))
    assert (
        cli.main(
            [
                "bootstrap",
                "--config",
                config_path,
                "--confirm-new-deployment",
                "--confirm-clean-production-profile",
            ]
        )
        == cli.EXIT_SUCCESS
    )
    assert cli.main(["preflight", "--config", config_path]) == cli.EXIT_SUCCESS


def test_cli_reports_lock_contention_with_its_own_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_build_anki", lambda config: FakeAnki([]))
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    holder = DeploymentLock(config.data_root / LOCK_FILE_NAME)
    holder.acquire()
    exit_code = cli.main(
        [
            "bootstrap",
            "--config",
            str(tmp_path / "runtime.json"),
            "--confirm-new-deployment",
            "--confirm-clean-production-profile",
        ]
    )
    assert exit_code == cli.EXIT_LOCK_CONTENTION
    holder.release()


def test_create_deployment_rejects_a_forged_precondition(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    layout = build_layout(config.data_root)
    forged = BootstrapPreconditions(
        layout=layout,
        preflight=run_bootstrap_preflight(config, FakeAnki([])),
        registry=(),
        registry_count=0,
        registry_digest=f"sha256:{canonical_sha256([])}",
        confirmations_present=False,
    )
    before = snapshot(tmp_path)
    with pytest.raises(RuntimeBootstrapError):
        create_deployment(forged)
    assert snapshot(tmp_path) == before


# ----------------------------------------------------------------------
# G-16: error taxonomy — defects must not masquerade as refusals
# ----------------------------------------------------------------------


def test_operational_anki_failure_is_normalized_to_a_refusal(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    anki = FakeAnki([make_note(1, "alpha::demo", "alpha")])

    def raise_operational(query: str) -> list[int]:
        raise AnkiConnectError("connection refused")

    anki.find_notes = raise_operational  # type: ignore[assignment]
    with pytest.raises(RuntimeBootstrapError):
        evaluate_preconditions(
            config,
            anki,
            confirm_new_deployment=True,
            confirm_clean_production_profile=True,
        )
    assert not config.data_root.exists()


@pytest.mark.parametrize(
    "defect",
    (
        pytest.param(TypeError("bad call"), id="TypeError"),
        pytest.param(AttributeError("missing attribute"), id="AttributeError"),
        pytest.param(AssertionError("invariant broken"), id="AssertionError"),
        pytest.param(KeyError("absent key"), id="KeyError"),
    ),
)
def test_programming_defects_surface_instead_of_becoming_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: BaseException
) -> None:
    """A defect reported as exit 1 is indistinguishable from a safe refusal.

    The oracle is that the defect's own type escapes ``cli.main`` rather than
    being converted into a return value, so that a bug looks like a bug.
    """

    def raise_defect(config: object) -> object:
        raise defect

    monkeypatch.setattr(cli, "_build_anki", raise_defect)
    write_config(tmp_path)
    with pytest.raises(type(defect)):
        cli.main(["preflight", "--config", str(tmp_path / "runtime.json")])


def test_cli_catches_no_broad_exception() -> None:
    """The composition root must not carry a catch-all handler."""
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "except Exception" not in source
    assert "except BaseException" not in source


# ----------------------------------------------------------------------
# G-8: I-2 stale lock requires human intervention (D70 s10)
# ----------------------------------------------------------------------


def test_stale_lock_at_i2_is_not_auto_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-1 may retry; I-2 may not, because a stale lock still has authority."""
    monkeypatch.setattr(cli, "_build_anki", lambda config: FakeAnki([]))
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    stale = config.data_root / LOCK_FILE_NAME
    stale.write_text(
        json.dumps({"pid": 999999, "acquired_utc": "2026-08-27T00:00:00+00:00"}),
        encoding="utf-8",
    )
    contents = stale.read_bytes()

    exit_code = cli.main(
        [
            "bootstrap",
            "--config",
            str(tmp_path / "runtime.json"),
            "--confirm-new-deployment",
            "--confirm-clean-production-profile",
        ]
    )
    assert exit_code == cli.EXIT_LOCK_CONTENTION
    assert stale.read_bytes() == contents
    assert missing_durable_entries(build_layout(config.data_root)) != ()


def test_human_removing_the_stale_lock_unblocks_bootstrap(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))
    config.data_root.mkdir(parents=True)
    stale = config.data_root / LOCK_FILE_NAME
    stale.write_text(
        json.dumps({"pid": 999999, "acquired_utc": "2026-08-27T00:00:00+00:00"}),
        encoding="utf-8",
    )
    stale.unlink()
    bootstrap_deployment(tmp_path)
    assert missing_durable_entries(build_layout(config.data_root)) == ()


# ----------------------------------------------------------------------
# BLOCKER 2: normalization is per seam, never a global ValueError catch
# ----------------------------------------------------------------------


def test_value_error_outside_an_approved_seam_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect that happens to raise ValueError must not become exit 1.

    Several core errors are ValueError subclasses, which is exactly why a
    global ValueError catch would be wrong: it would convert an ordinary bug
    into a fail-closed refusal indistinguishable from a deliberate one.
    """

    def defective_layout(data_root: object) -> object:
        raise ValueError("synthetic defect outside any operational seam")

    monkeypatch.setattr(cli, "build_layout", defective_layout)
    monkeypatch.setattr(cli, "_build_anki", lambda config: FakeAnki([]))
    write_config(tmp_path)
    with pytest.raises(ValueError, match="synthetic defect"):
        cli.main(["preflight", "--config", str(tmp_path / "runtime.json")])


def test_normalization_has_no_global_taxonomy() -> None:
    from vocab.runtime import normalize

    assert not hasattr(normalize, "OPERATIONAL_EXCEPTIONS")
    for seam in (
        normalize.ANKI_SEAM,
        normalize.CORPUS_SEAM,
        normalize.ARTIFACT_SEAM,
        normalize.LEDGER_SEAM,
    ):
        assert ValueError not in seam
    assert normalize.FILESYSTEM_SEAM == (OSError,)


def test_seam_normalization_requires_an_explicit_family() -> None:
    from vocab.runtime.normalize import ANKI_SEAM, normalized

    with pytest.raises(TypeError):
        with normalized(RuntimeBootstrapError, "no family declared"):  # type: ignore[call-arg]
            pass
    with pytest.raises(RuntimeBootstrapError):
        with normalized(RuntimeBootstrapError, "anki", catching=ANKI_SEAM):
            raise AnkiConnectError("unreachable")


# ----------------------------------------------------------------------
# VERIFICATION ITEM: ArtifactStore construction must not recreate the root
# ----------------------------------------------------------------------


def test_standalone_preflight_never_recreates_a_missing_artifact_root(
    tmp_path: Path,
) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    shutil.rmtree(layout.artifact_root)
    before = snapshot(config.data_root)

    report = run_standalone_preflight(config, layout, FakeAnki([]))

    assert not report.ok
    assert not layout.artifact_root.exists()
    assert snapshot(config.data_root) == before


def test_write_preflight_never_recreates_a_missing_artifact_root(
    tmp_path: Path,
) -> None:
    """ArtifactStore.__init__ mkdirs, so the root is verified before use."""
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    shutil.rmtree(layout.artifact_root)
    before = snapshot(config.data_root)

    with DeploymentLock(layout.lock_path):
        report = run_runtime_write_preflight(config, layout, FakeAnki([]))

    statuses = {check.name: check.status for check in report.checks}
    assert statuses["layout.durable_entries"] == FAIL
    assert statuses["t12.triple_consistency"] == FAIL
    assert not layout.artifact_root.exists()
    assert snapshot(config.data_root) == before


def test_cli_preflight_refuses_a_missing_artifact_root(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    shutil.rmtree(build_layout(config.data_root).artifact_root)
    before = snapshot(config.data_root)
    assert cli.main(["preflight", "--config", str(tmp_path / "runtime.json")]) == (
        cli.EXIT_REFUSED
    )
    assert not (config.data_root / "artifacts").exists()
    assert snapshot(config.data_root) == before


# ----------------------------------------------------------------------
# BLOCKER 1: the authority strict-reads before returning
# ----------------------------------------------------------------------


def test_authority_strict_reads_before_returning(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    layout.event_log_path.write_text('{"torn": true}', encoding="utf-8")
    before = layout.event_log_path.read_bytes()

    with pytest.raises(RuntimeEventLogError, match="could not be acquired and read"):
        open_runtime_event_log(layout.event_log_path)

    assert layout.event_log_path.read_bytes() == before


def test_authority_returns_a_readable_journal(tmp_path: Path) -> None:
    config = bootstrap_deployment(tmp_path)
    layout = build_layout(config.data_root)
    journal = open_runtime_event_log(layout.event_log_path)
    assert journal.read_strict() == []
    assert journal.path == layout.event_log_path


def test_authority_source_shape_is_frozen() -> None:
    """The acquisition, the strict read, and the return share one object."""
    import ast

    from vocab.runtime import eventlog_authority

    tree = ast.parse(Path(eventlog_authority.__file__).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    attempt, ending = function.body[-2:]
    assert isinstance(attempt, ast.Try)
    assignment, read_statement = attempt.body
    assert isinstance(assignment, ast.Assign)
    name = assignment.targets[0].id  # type: ignore[union-attr]
    assert assignment.value.func.attr == "open_existing"  # type: ignore[union-attr]
    assert assignment.value.func.value.id == "EventLog"  # type: ignore[union-attr]
    call = read_statement.value  # type: ignore[union-attr]
    assert call.func.attr == "read_strict"  # type: ignore[union-attr]
    assert call.func.value.id == name  # type: ignore[union-attr]
    assert isinstance(ending, ast.Return)
    assert ending.value.id == name  # type: ignore[union-attr]
    caught = {element.id for element in attempt.handlers[0].type.elts}  # type: ignore[union-attr]
    assert caught == {
        "EventLogCorruptionError",
        "UnsupportedEventVersionError",
        "OSError",
    }


def test_authority_never_calls_the_constructor() -> None:
    """P1a stays in force: the constructor creates files, so it is unusable."""
    source = Path(
        __import__("vocab.runtime.eventlog_authority", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    assert "EventLog(path)" not in source
    assert "EventLog.open_existing(path)" in source


# ----------------------------------------------------------------------
# Directive section 9: mandatory acquisition and append-hardening tests
# ----------------------------------------------------------------------


def test_open_existing_refuses_a_missing_path_and_creates_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "events.jsonl"
    with pytest.raises(OSError):
        EventLog.open_existing(target)
    assert not target.exists()


def test_open_existing_refuses_a_non_regular_path(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        EventLog.open_existing(tmp_path)


def test_open_existing_returns_the_exact_class(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    EventLog(target)
    journal = EventLog.open_existing(target)
    assert type(journal) is EventLog
    assert journal.path == target
    assert journal.read_strict() == []


def test_disappearance_before_acquisition_fails_without_recreating(
    tmp_path: Path,
) -> None:
    """Acquisition has no check-then-create window.

    open_existing opens without O_CREAT, so a path removed between any earlier
    validation and the open simply fails. The oracle is that no journal is left
    behind, because an empty history reads clean and would look healthy.
    """
    target = tmp_path / "events.jsonl"
    EventLog(target)
    target.unlink()
    with pytest.raises(RuntimeEventLogError):
        open_runtime_event_log(target)
    assert not target.exists()


def test_log_never_recreates_a_deleted_journal(tmp_path: Path) -> None:
    """D70 supersedes generic log() behaviour only for this case."""
    target = tmp_path / "events.jsonl"
    journal = EventLog(target)
    payload = {"count": 1, "source": "s", "month": "2026-08", "scan_version": 1}
    journal.log("ENCOUNTER", "alpha::demo", payload)
    assert len(target.read_bytes().splitlines()) == 1

    target.unlink()
    with pytest.raises(OSError):
        journal.log("ENCOUNTER", "alpha::demo", payload)
    assert not target.exists()


def test_log_still_appends_normally(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    journal = EventLog(target)
    payload = {"count": 1, "source": "s", "month": "2026-08", "scan_version": 1}
    journal.log("ENCOUNTER", "alpha::demo", payload)
    journal.log("ENCOUNTER", "beta::demo", payload)
    assert len(journal.read_strict()) == 2
