"""D69 official-acceptance certification gate."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import pytest

from tests.d58_probe_inventory import REQUIRED_D58_SELECTORS


_REQUIRED_PHASES = ("setup", "call", "teardown")
_NATURAL_DISCOVERY_DEFAULTS = {
    "testpaths": (),
    "norecursedirs": (
        "*.egg",
        ".*",
        "_darcs",
        "build",
        "CVS",
        "dist",
        "node_modules",
        "venv",
        "{arch}",
    ),
    "python_files": ("test_*.py", "*_test.py"),
    "python_classes": ("Test",),
    "python_functions": ("test",),
    "collect_imported_tests": True,
}


@dataclass
class _AcceptanceState:
    official: bool = False
    filter_reasons: tuple[str, ...] = ()
    required: set[str] = field(default_factory=set)
    missing_selectors: set[str] = field(default_factory=set)
    deselected: set[str] = field(default_factory=set)
    reports: dict[str, dict[str, tuple[str, object]]] = field(
        default_factory=lambda: defaultdict(dict)
    )


_STATE = _AcceptanceState()


def _is_official_acceptance_args(args: Iterable[str]) -> bool:
    return tuple(args) == ()


def _effective_filter_reasons(config: pytest.Config) -> tuple[str, ...]:
    """Return every effective reason this is not the exact full-suite run."""
    reasons: list[str] = []
    invocation_args = tuple(config.invocation_params.args)
    if not _is_official_acceptance_args(invocation_args):
        reasons.append(f"explicit pytest arguments: {invocation_args!r}")

    args_source = getattr(config, "args_source", None)
    args_source_name = getattr(args_source, "name", None)
    if args_source_name != "INVOCATION_DIR":
        reasons.append(
            "effective collection source is not the invocation directory: "
            f"{args_source_name!r}"
        )

    for name, natural_default in _NATURAL_DISCOVERY_DEFAULTS.items():
        effective = config.getini(name)
        normalized = tuple(effective) if isinstance(natural_default, tuple) else effective
        if normalized != natural_default:
            reasons.append(
                f"effective discovery {name}={effective!r}; "
                f"natural default={natural_default!r}"
            )

    # Pytest also supports conftest-level collection exclusions outside ini.
    # Reject any effective non-empty declaration from loaded plugins.
    for plugin in config.pluginmanager.get_plugins():
        for name in ("collect_ignore", "collect_ignore_glob"):
            value = getattr(plugin, name, None)
            if value:
                reasons.append(
                    f"effective {name}={value!r} from loaded collection plugin"
                )

    selection_options = (
        ("keyword", "-k"),
        ("markexpr", "-m"),
        ("deselect", "--deselect"),
        ("lf", "--lf"),
        ("failedfirst", "--ff"),
        ("newfirst", "--nf"),
        ("ignore", "--ignore"),
        ("ignore_glob", "--ignore-glob"),
        ("collectonly", "--collect-only"),
        ("pyargs", "--pyargs"),
        ("stepwise", "--stepwise"),
        ("stepwise_skip", "--stepwise-skip"),
    )
    for option_name, display_name in selection_options:
        value = getattr(config.option, option_name, None)
        if value:
            reasons.append(f"effective {display_name}={value!r}")
    return tuple(reasons)


def _selector_matches(nodeid: str, selector: str) -> bool:
    return nodeid == selector or nodeid.startswith(selector + "[")


def _acceptance_failures(
    *,
    required: set[str],
    missing_selectors: set[str],
    deselected: set[str],
    reports: dict[str, dict[str, tuple[str, object]]],
) -> list[str]:
    failures = [
        *(f"registered selector collected zero items: {item}" for item in sorted(missing_selectors)),
        *(f"required item was deselected: {item}" for item in sorted(deselected)),
    ]
    for nodeid in sorted(required):
        phases = reports.get(nodeid, {})
        for phase in _REQUIRED_PHASES:
            result = phases.get(phase)
            if result is None:
                failures.append(f"required item has no {phase} report: {nodeid}")
                continue
            outcome, wasxfail = result
            if outcome != "passed" or wasxfail is not None:
                failures.append(
                    f"required item {phase} did not pass normally: {nodeid} "
                    f"outcome={outcome!r} wasxfail={wasxfail!r}"
                )
    return failures


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    global _STATE
    filter_reasons = _effective_filter_reasons(config)
    _STATE = _AcceptanceState(
        official=not filter_reasons,
        filter_reasons=filter_reasons,
    )
    if not _STATE.official:
        return
    nodeids = tuple(item.nodeid for item in items)
    for selector in REQUIRED_D58_SELECTORS:
        matches = {
            nodeid for nodeid in nodeids if _selector_matches(nodeid, selector)
        }
        if not matches:
            _STATE.missing_selectors.add(selector)
        _STATE.required.update(matches)


def pytest_deselected(items: list[pytest.Item]) -> None:
    if _STATE.official:
        _STATE.deselected.update(
            item.nodeid for item in items if item.nodeid in _STATE.required
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _STATE.official and report.nodeid in _STATE.required:
        _STATE.reports[report.nodeid][report.when] = (
            report.outcome,
            getattr(report, "wasxfail", None),
        )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if not _STATE.official:
        if reporter is not None:
            reporter.write_line(
                "D69 acceptance not certified: pytest invocation was filtered "
                "or selected by effective configuration."
            )
            for reason in _STATE.filter_reasons:
                reporter.write_line(f"effective filter: {reason}")
        return
    failures = _acceptance_failures(
        required=_STATE.required,
        missing_selectors=_STATE.missing_selectors,
        deselected=_STATE.deselected,
        reports=_STATE.reports,
    )
    if failures:
        if reporter is not None:
            reporter.write_sep("=", "D69 acceptance gate failures")
            for failure in failures:
                reporter.write_line(failure)
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    elif exitstatus != pytest.ExitCode.OK or session.exitstatus != pytest.ExitCode.OK:
        if reporter is not None:
            reporter.write_line(
                "D69 acceptance not certified: full pytest session exit status "
                f"was {int(session.exitstatus)}."
            )
    elif reporter is not None:
        reporter.write_line(
            f"D69 acceptance certified: {len(_STATE.required)} required items passed."
        )
