"""Registered D69 seam, authority, downgrade, and acceptance-gate probes."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import conftest as acceptance_plugin
import vocab.assessment_producer as producer_module
import vocab.events as events_module
import vocab.reconcile as reconcile_module
from tests.t12_ast_invariants import (
    APPROVED_LIFECYCLE_HISTORY_READS,
    APPROVED_STATE_MATERIALIZERS,
    assert_d58_probe_invariants,
    assert_t12_ast_invariants,
)
from tests.test_reconcile_observation import NOTE_ID, default_anki
from vocab.contracts import (
    ASSESSMENT_OUTCOME_ABSTAIN,
    ASSESSMENT_OUTCOME_FAIL,
    ASSESSMENT_OUTCOME_OMITTED,
    ASSESSMENT_OUTCOME_PASS,
    T12_ASSESSMENT_PRODUCER_ID,
    T12_ASSESSMENT_PRODUCER_VERSION,
    T12_LIFECYCLE_ENABLED_CHANNELS,
    T12_LIFECYCLE_EVENT_SCHEMA_VERSION,
)
from vocab.events import EventLog
from vocab.models import Event
from vocab.reconcile import ReconcileEventHistoryError, ReconcileRecoveryError


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
UNIT_KEY = "subtle::small-difference"


def _event(payload: dict[str, object], *, version: int = 1) -> Event:
    return Event(
        v=version,
        ts="2026-08-26T01:00:00+00:00",
        day="2026-08-26",
        event="JUDGE",
        unit_key=UNIT_KEY,
        payload=payload,
    )


def _t12_payload(
    *,
    outcome: str = ASSESSMENT_OUTCOME_PASS,
    channel: str = "R",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "producer": T12_ASSESSMENT_PRODUCER_ID,
        "producer_version": T12_ASSESSMENT_PRODUCER_VERSION,
        "attempt_id": "attempt:v1:" + "1" * 64,
        "channel": channel,
        "presented_stimulus_ref": "stimulus:v1:" + "2" * 64,
        "outcome": outcome,
        "passed": outcome == ASSESSMENT_OUTCOME_PASS,
        "model_id": "probe-model",
        "model_version": "1",
        "authority_kind": "semantic_model",
        "provenance": {"probe": True},
    }
    if outcome in (ASSESSMENT_OUTCOME_PASS, ASSESSMENT_OUTCOME_FAIL):
        payload.update(
            {
                "assessment_id": payload["attempt_id"],
                "stimulus_ref": payload["presented_stimulus_ref"],
                "novel": True,
            }
        )
    return payload


def _classify_strict_read(reader) -> str:
    try:
        reader()
    except Exception:
        return "REJECT"
    return "ACCEPT"


def _copy_vocab(tmp_path: Path) -> Path:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "vocab", tmp_path / "vocab")
    return tmp_path


def test_d69_static_invariants_accept_real_tree() -> None:
    repository = Path(__file__).resolve().parents[1]
    assert T12_LIFECYCLE_ENABLED_CHANNELS == ("R", "L", "W", "S")
    assert (
        T12_LIFECYCLE_EVENT_SCHEMA_VERSION
        == producer_module.T12_PRODUCER_EVENT_SCHEMA_VERSION
        == 1
    )
    assert len(APPROVED_LIFECYCLE_HISTORY_READS) == 2
    assert len(APPROVED_STATE_MATERIALIZERS) == 7
    assert_t12_ast_invariants(repository)


@pytest.mark.parametrize(
    ("case", "accepted"),
    (
        ("pass", True),
        ("fail", True),
        ("abstain", True),
        ("omitted", True),
        ("abstain_d35", False),
        ("omitted_d35", False),
        ("unknown_producer", False),
        ("stripped_producer", False),
        ("unsupported_channel", False),
        ("bad_passed", False),
        ("bad_envelope", False),
    ),
)
def test_t12_lifecycle_consumer_gate(case: str, accepted: bool) -> None:
    outcome = {
        "fail": ASSESSMENT_OUTCOME_FAIL,
        "abstain": ASSESSMENT_OUTCOME_ABSTAIN,
        "abstain_d35": ASSESSMENT_OUTCOME_ABSTAIN,
        "omitted": ASSESSMENT_OUTCOME_OMITTED,
        "omitted_d35": ASSESSMENT_OUTCOME_OMITTED,
    }.get(case, ASSESSMENT_OUTCOME_PASS)
    payload = _t12_payload(outcome=outcome)
    version = 1
    if case in ("abstain_d35", "omitted_d35"):
        payload.update(
            {
                "assessment_id": payload["attempt_id"],
                "stimulus_ref": payload["presented_stimulus_ref"],
                "novel": True,
            }
        )
    elif case == "unknown_producer":
        payload["producer"] = "unknown"
    elif case == "stripped_producer":
        del payload["producer"]
    elif case == "unsupported_channel":
        payload["channel"] = "X"
    elif case == "bad_passed":
        payload["passed"] = False
    elif case == "bad_envelope":
        version = 2

    if accepted:
        parsed = reconcile_module._lifecycle_assessment(
            _event(payload, version=version),
            7,
            NOW,
        )
        if outcome in (ASSESSMENT_OUTCOME_ABSTAIN, ASSESSMENT_OUTCOME_OMITTED):
            assert parsed is None
        else:
            assert parsed is not None
            assert parsed[2].passed is (outcome == ASSESSMENT_OUTCOME_PASS)
    else:
        with pytest.raises(
            ReconcileEventHistoryError,
            match="unit_key=.*event index=7",
        ):
            reconcile_module._lifecycle_assessment(
                _event(payload, version=version),
                7,
                NOW,
            )


def test_legacy_generic_judge_remains_compatible() -> None:
    payload = {
        "channel": "R",
        "passed": True,
        "model_id": "legacy-model",
        "model_version": "legacy-version",
        "assessment_id": "legacy-assessment",
        "stimulus_ref": "legacy-stimulus",
        "novel": True,
    }
    parsed = reconcile_module._lifecycle_assessment(_event(payload), 3, NOW)
    assert parsed is not None
    assert parsed[2].assessment_id == "legacy-assessment"


def test_frozen_t12_v1_survives_future_generic_schema_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    monkeypatch.setattr(events_module, "_now_utc", lambda: NOW)
    stored = log.log("JUDGE", UNIT_KEY, _t12_payload())
    assert stored.v == 1

    decode_calls = 0
    v1_decoder = events_module._EVENT_DECODERS[1]

    def tracked_v1_decoder(record, *, location):
        nonlocal decode_calls
        decode_calls += 1
        return v1_decoder(record, location=location)

    monkeypatch.setitem(events_module._EVENT_DECODERS, 1, tracked_v1_decoder)
    monkeypatch.setattr(events_module, "EVENT_SCHEMA_VERSION", 2)
    monkeypatch.setattr(reconcile_module, "EVENT_SCHEMA_VERSION", 2)
    monkeypatch.setitem(
        events_module._EVENT_DECODERS,
        2,
        v1_decoder,
    )

    decoded = log.read_strict()
    assert len(decoded) == 1
    assert decoded[0].v == 1
    progress = reconcile_module.observe_unit(
        NOTE_ID,
        anki=default_anki(),
        event_log=log,
        now=NOW + timedelta(seconds=1),
    )
    assessments = progress.channels[0].assessments
    assert len(assessments) == 1
    assert assessments[0].passed is True
    assert decode_calls == 2
    assert T12_LIFECYCLE_EVENT_SCHEMA_VERSION == 1


def test_frozen_t12_v1_probe_requires_v1_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    monkeypatch.setattr(events_module, "_now_utc", lambda: NOW)
    assert log.log("JUDGE", UNIT_KEY, _t12_payload()).v == 1
    monkeypatch.delitem(events_module._EVENT_DECODERS, 1)

    with pytest.raises(events_module.UnsupportedEventVersionError, match="version 1"):
        log.read_strict()


@pytest.mark.parametrize(
    "history_kind",
    (
        "empty",
        "one_valid",
        "multiple_valid",
        "no_final_newline",
        "malformed_final",
        "malformed_interior",
        "invalid_utf8_final",
        "invalid_utf8_interior",
        "unsupported_version_final",
        "unsupported_version_interior",
        "invalid_envelope_final",
    ),
)
def test_strict_reader_equivalence_table(tmp_path: Path, history_kind: str) -> None:
    seed = EventLog(tmp_path / "seed.jsonl")
    seed.log(
        "ENCOUNTER",
        UNIT_KEY,
        {"count": 1, "source": "probe", "month": "2026-08"},
    )
    valid = seed.path.read_bytes()
    value = json.loads(valid.decode("utf-8"))
    value["v"] = 2
    unsupported = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
    histories = {
        "empty": b"",
        "one_valid": valid,
        "multiple_valid": valid + valid,
        "no_final_newline": valid.removesuffix(b"\n"),
        "malformed_final": valid + b'{"broken":\n',
        "malformed_interior": b'{"broken":\n' + valid,
        "invalid_utf8_final": valid + b"\xff\n",
        "invalid_utf8_interior": b"\xff\n" + valid,
        "unsupported_version_final": valid + unsupported,
        "unsupported_version_interior": unsupported + valid,
        "invalid_envelope_final": valid + b"{}\n",
    }
    left = EventLog(tmp_path / "strict.jsonl")
    right = EventLog(tmp_path / "producer.jsonl")
    left.path.write_bytes(histories[history_kind])
    right.path.write_bytes(histories[history_kind])

    assert _classify_strict_read(left.read_strict) == _classify_strict_read(
        lambda: producer_module._strict_read_event_history(right)
    )


def test_torn_tail_blocks_recovery_read(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.path.write_bytes(b'{"torn":')
    with pytest.raises(ReconcileRecoveryError, match="recovery scan failed"):
        reconcile_module._read_recovery_transactions(UNIT_KEY, log, NOW)


def test_torn_tail_blocks_lifecycle_read(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.path.write_bytes(b'{"torn":')
    with pytest.raises(ReconcileEventHistoryError, match="cannot be read"):
        reconcile_module.observe_unit(
            NOTE_ID,
            anki=default_anki(),
            event_log=log,
            now=NOW,
        )


@pytest.mark.parametrize("mutation", ("read", "capture", "duplicate", "moved"))
def test_lifecycle_read_matrix_rejects_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _copy_vocab(tmp_path)
    target = root / "vocab/reconcile.py"
    source = target.read_text(encoding="utf-8")
    old = "events = event_log.read_strict()"
    assert source.count(old) == 2
    if mutation == "read":
        source = source.replace(old, "events = event_log.read()", 1)
    elif mutation == "capture":
        source = source.replace(
            old,
            "reader = event_log.read_strict\n        events = reader()",
            1,
        )
    elif mutation == "duplicate":
        source = source.replace(
            old,
            old + "\n        event_log.read_strict()",
            1,
        )
    else:
        source += "\n\ndef rogue_read(event_log):\n    return event_log.read_strict()\n"
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match="read"):
        assert_t12_ast_invariants(root)


@pytest.mark.parametrize(
    "mutation",
    ("literal", "computed", "keyword", "captured", "duplicate", "removed", "moved"),
)
def test_state_materializer_matrix_rejects_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _copy_vocab(tmp_path)
    target = root / "vocab/reconcile.py"
    source = target.read_text(encoding="utf-8")
    block = """_append_state_event(
            event_log,
            unit_key,
            plan,
            T9_STATE_PHASE_PREPARE,
        )"""
    assert block in source
    if mutation == "literal":
        source = source.replace(
            block,
            block.replace("T9_STATE_PHASE_PREPARE,", '"PREPARE",'),
            1,
        )
    elif mutation == "computed":
        source = source.replace(
            block,
            block.replace(
                "T9_STATE_PHASE_PREPARE,",
                'T9_STATE_PHASE_PREPARE + "",',
            ),
            1,
        )
    elif mutation == "keyword":
        source = source.replace(
            block,
            block.replace(
                "            T9_STATE_PHASE_PREPARE,",
                "            phase=T9_STATE_PHASE_PREPARE,",
            ),
            1,
        )
    elif mutation == "captured":
        marker = "def _materialize_ungrouped_plan("
        source = source.replace(
            marker,
            "_captured_state_writer = _append_state_event\n\n\n" + marker,
            1,
        )
    elif mutation == "duplicate":
        source = source.replace(block, block + "\n        " + block, 1)
    elif mutation == "removed":
        source = source.replace(block, "pass", 1)
    else:
        source = source.replace(block, "pass", 1)
        source += (
            "\n\ndef rogue_state(event_log, unit_key, plan):\n"
            "    _append_state_event(event_log, unit_key, plan, "
            "T9_STATE_PHASE_PREPARE)\n"
        )
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match="STATE materializer"):
        assert_t12_ast_invariants(root)


@pytest.mark.parametrize(
    "snippet",
    (
        "captured = _append_state_event",
        "writers = [_append_state_event]",
        "identity(_append_state_event)",
        "globals()['\u005fappend_state_event']",
        "vars(module)['\u005fappend_state_event']",
        "getattr(module, '\u005fappend_state_event')",
        "locals().get('\u005fappend_state_event')",
        "module.__dict__['\u005fappend_state_event']",
    ),
)
def test_state_materializer_indirect_authority_rejects_mutations(
    tmp_path: Path,
    snippet: str,
) -> None:
    root = _copy_vocab(tmp_path)
    target = root / "vocab/reconcile.py"
    source = target.read_text(encoding="utf-8")
    marker = "def _materialize_ungrouped_plan("
    assert marker in source
    target.write_text(
        source.replace(marker, snippet + "\n\n\n" + marker, 1),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="STATE materializer"):
        assert_t12_ast_invariants(root)


@pytest.mark.parametrize(
    "statement",
    (
        "import vocab.events",
        "import vocab.events as ev",
        "import events",
        "from .events import EventLogCorruptionWarning",
        "from .events import EventLogCorruptionError",
        "from vocab.events import UnsupportedEventVersionError",
        "from .events import *",
        "from . import events",
        "from vocab import events",
        "from ..vocab.events import EventLogCorruptionError",
    ),
)
def test_concrete_events_import_allowlist_rejects_all_forms(
    tmp_path: Path,
    statement: str,
) -> None:
    root = _copy_vocab(tmp_path)
    target = root / "vocab/artifact_store.py"
    source = target.read_text(encoding="utf-8")
    marker = "from __future__ import annotations"
    target.write_text(
        source.replace(marker, marker + "\n\n" + statement, 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="concrete EventLog import"):
        assert_t12_ast_invariants(root)


def test_p1a_rejects_production_eventlog_construction(tmp_path: Path) -> None:
    root = _copy_vocab(tmp_path)
    target = root / "vocab/assessment_producer.py"
    source = target.read_text(encoding="utf-8")
    marker = "def _entry_gate("
    target.write_text(
        source.replace(
            marker,
            "_forbidden_log = EventLog('deployment.jsonl')\n\n\n" + marker,
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="EventLog construction"):
        assert_t12_ast_invariants(root)


def test_registered_probe_inventory_is_closed_and_unskippable() -> None:
    assert_d58_probe_invariants(Path(__file__).resolve().parents[1])


def _remove_mapping_entry(source: str, mapping_name: str, key: str) -> str:
    tree = ast.parse(source)
    mapping = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == mapping_name
            for target in node.targets
        )
    )
    assert isinstance(mapping, ast.Dict)
    for key_node, value_node in zip(mapping.keys, mapping.values, strict=True):
        if isinstance(key_node, ast.Constant) and key_node.value == key:
            lines = source.splitlines(keepends=True)
            del lines[key_node.lineno - 1 : value_node.end_lineno]
            return "".join(lines)
    raise AssertionError(f"mapping key not found: {key}")


@pytest.mark.parametrize("mutation", ("removed", "unknown", "renamed"))
def test_registered_probe_inventory_rejects_registry_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "tests", tmp_path / "tests")
    target = tmp_path / "tests/d58_probe_inventory.py"
    source = target.read_text(encoding="utf-8")
    clause = "producer_text_exact_rerun_appends_zero"
    if mutation == "removed":
        mutated = _remove_mapping_entry(source, "D58_PROBE_INVENTORY", clause)
    elif mutation == "unknown":
        marker = "D58_PROBE_INVENTORY = {\n"
        addition = (
            '    "unknown_registry_clause": (\n'
            '        "tests/test_t12_producer.py::test_text_missing_then_exact_rerun",\n'
            "    ),\n"
        )
        mutated = source.replace(marker, marker + addition, 1)
    else:
        registry_offset = source.index("D58_PROBE_INVENTORY = {")
        before, registry = source[:registry_offset], source[registry_offset:]
        registry = registry.replace(
            f'    "{clause}": (',
            f'    "{clause}_renamed": (',
            1,
        )
        mutated = before + registry
    target.write_text(mutated, encoding="utf-8")

    with pytest.raises(AssertionError, match="frozen clause set"):
        assert_d58_probe_invariants(tmp_path)


def test_semantic_anchor_matrix_rejects_source_mutation(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "tests", tmp_path / "tests")
    target = tmp_path / "tests/test_t11_invariant_probes.py"
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    anchors = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target_node, ast.Name)
            and target_node.id == "SEMANTIC_ANCHORS"
            for target_node in node.targets
        )
    )
    assert isinstance(anchors, ast.Tuple)
    first = anchors.elts[0]
    lines = source.splitlines(keepends=True)
    del lines[first.lineno - 1 : first.end_lineno]
    target.write_text("".join(lines), encoding="utf-8")

    with pytest.raises(AssertionError, match="SEMANTIC_ANCHORS"):
        assert_d58_probe_invariants(tmp_path)


@pytest.mark.parametrize(
    "snippet",
    (
        'pytestmark = pytest.mark.skip(reason="disabled")',
        'pytest.mark.skip(reason="disabled")',
        'pytest.mark.skipif(True, reason="disabled")',
        'pytest.mark.xfail(reason="disabled")',
        'unittest.skip("disabled")',
        'pytest.skip("disabled")',
        'pytest.xfail("disabled")',
        'pytest.importorskip("missing")',
        'from pytest import skip as ignored\nignored("disabled")',
        'from unittest import skipIf as ignored\nignored(True, "disabled")',
    ),
)
def test_registered_probe_unskippability_rejects_mutations(
    tmp_path: Path,
    snippet: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "tests", tmp_path / "tests")
    target = tmp_path / "tests/test_t12_real_smoke.py"
    source = target.read_text(encoding="utf-8")
    target.write_text(source + "\n" + snippet + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="skip|xfail|pytestmark"):
        assert_d58_probe_invariants(tmp_path)


@pytest.mark.parametrize(
    ("reports", "expected"),
    (
        (
            {
                "required": {
                    "setup": ("passed", None),
                    "call": ("passed", "unexpected pass"),
                    "teardown": ("passed", None),
                }
            },
            "wasxfail",
        ),
        (
            {
                "required": {
                    "setup": ("skipped", None),
                    "call": ("passed", None),
                    "teardown": ("passed", None),
                }
            },
            "setup",
        ),
        (
            {
                "required": {
                    "setup": ("passed", None),
                    "teardown": ("passed", None),
                }
            },
            "no call report",
        ),
    ),
)
def test_acceptance_gate_rejects_nonpassing_required_phases(
    reports: dict[str, dict[str, tuple[str, object]]],
    expected: str,
) -> None:
    failures = acceptance_plugin._acceptance_failures(
        required={"required"},
        missing_selectors=set(),
        deselected=set(),
        reports=reports,
    )
    assert any(expected in failure for failure in failures)


@pytest.mark.parametrize(
    "args",
    (
        ("-k", "probe"),
        ("-m", "probe"),
        ("--deselect", "tests/test_x.py::test_x"),
        ("--lf",),
        ("--ff",),
        ("--nf",),
        ("--ignore=tests/test_x.py",),
        ("--ignore-glob=test_*.py",),
        ("--collect-only",),
        ("tests/test_x.py",),
        ("tests/test_x.py::test_x",),
    ),
)
def test_filtered_pytest_arguments_cannot_certify_acceptance(
    args: tuple[str, ...],
) -> None:
    assert acceptance_plugin._is_official_acceptance_args(())
    assert not acceptance_plugin._is_official_acceptance_args(args)


def _acceptance_subprocess(
    tmp_path: Path,
    *,
    pytest_addopts: str = "",
    configured_addopts: str = "",
    discovery_case: str = "",
    unrelated_fails: bool = False,
    deselect_parameter: bool = False,
) -> subprocess.CompletedProcess[str]:
    repository = Path(__file__).resolve().parents[1]
    shutil.copyfile(repository / "conftest.py", tmp_path / "conftest.py")
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "__init__.py").write_text("", encoding="utf-8")
    required_relative = (
        Path("tests/required_only_area/test_required.py")
        if discovery_case == "testpaths"
        else Path("tests/test_required.py")
    )
    (tests_root / "d58_probe_inventory.py").write_text(
        "REQUIRED_D58_SELECTORS = frozenset({"
        f"'{required_relative.as_posix()}::test_required'}})\n",
        encoding="utf-8",
    )
    required_path = tmp_path / required_relative
    required_path.parent.mkdir(parents=True, exist_ok=True)
    required_path.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('case', ('a', 'b'))\n"
        "def test_required(case):\n"
        "    assert case in {'a', 'b'}\n",
        encoding="utf-8",
    )
    unrelated_assertion = "False" if unrelated_fails else "True"
    if discovery_case == "testpaths":
        unrelated_path = tests_root / "unrelated_area/test_unrelated.py"
        unrelated_source = "def test_unrelated():\n"
    elif discovery_case == "norecursedirs":
        unrelated_path = tests_root / "excluded/test_unrelated.py"
        unrelated_source = "def test_unrelated():\n"
    elif discovery_case == "python_files":
        unrelated_path = tests_root / "unrelated_test.py"
        unrelated_source = "def test_unrelated():\n"
    elif discovery_case == "python_classes":
        unrelated_path = tests_root / "test_unrelated.py"
        unrelated_source = "class TestUnrelated:\n    def test_failure(self):\n"
    else:
        unrelated_path = tests_root / "test_unrelated.py"
        unrelated_source = "def test_unrelated():\n"
    unrelated_path.parent.mkdir(parents=True, exist_ok=True)
    indent = "        " if discovery_case == "python_classes" else "    "
    unrelated_path.write_text(
        unrelated_source + f"{indent}assert {unrelated_assertion}\n",
        encoding="utf-8",
    )
    if deselect_parameter:
        (tmp_path / "deselect_plugin.py").write_text(
            "import pytest\n\n"
            "@pytest.hookimpl(trylast=True)\n"
            "def pytest_collection_modifyitems(config, items):\n"
            "    deselected = [item for item in items "
            "if item.nodeid.endswith('[b]')]\n"
            "    items[:] = [item for item in items if item not in deselected]\n"
            "    config.hook.pytest_deselected(items=deselected)\n",
            encoding="utf-8",
        )
        with (tmp_path / "conftest.py").open("a", encoding="utf-8") as handle:
            handle.write("\npytest_plugins = ('deselect_plugin',)\n")
    discovery_ini = {
        "testpaths": "testpaths = tests/required_only_area",
        "norecursedirs": "norecursedirs = excluded",
        "python_files": "python_files = test_*.py",
        "python_functions": "python_functions = test_required",
        "python_classes": "python_classes = RequiredOnly",
    }.get(discovery_case, "")
    if configured_addopts or discovery_ini:
        config_lines = ["[pytest]"]
        if configured_addopts:
            config_lines.append("addopts = " + configured_addopts)
        if discovery_ini:
            config_lines.append(discovery_ini)
        (tmp_path / "pytest.ini").write_text(
            "\n".join(config_lines) + "\n",
            encoding="utf-8",
        )

    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    if pytest_addopts:
        environment["PYTEST_ADDOPTS"] = pytest_addopts
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("source", "value"),
    (
        ("environment", "-k required"),
        ("environment", "--ignore=tests/test_unrelated.py"),
        ("configuration", "-k required"),
    ),
)
def test_hidden_pytest_filtering_cannot_certify_acceptance(
    tmp_path: Path,
    source: str,
    value: str,
) -> None:
    result = _acceptance_subprocess(
        tmp_path,
        pytest_addopts=value if source == "environment" else "",
        configured_addopts=value if source == "configuration" else "",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "D69 acceptance not certified" in output
    assert "D69 acceptance certified" not in output


@pytest.mark.parametrize(
    "discovery_case",
    ("testpaths", "norecursedirs", "python_files", "python_functions", "python_classes"),
)
def test_discovery_configuration_cannot_certify_acceptance(
    tmp_path: Path,
    discovery_case: str,
) -> None:
    result = _acceptance_subprocess(
        tmp_path,
        discovery_case=discovery_case,
        unrelated_fails=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "D69 acceptance not certified" in output
    assert "D69 acceptance certified" not in output
    if discovery_case == "testpaths":
        assert "effective collection source is not the invocation directory: 'TESTPATHS'" in output


def test_registered_param_deselection_cannot_certify_acceptance(
    tmp_path: Path,
) -> None:
    result = _acceptance_subprocess(tmp_path, deselect_parameter=True)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "required item was deselected" in output
    assert "D69 acceptance certified" not in output


def test_unrelated_regression_failure_cannot_certify_acceptance(
    tmp_path: Path,
) -> None:
    result = _acceptance_subprocess(tmp_path, unrelated_fails=True)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "D69 acceptance not certified" in output
    assert "full pytest session exit status" in output
    assert "D69 acceptance certified" not in output
