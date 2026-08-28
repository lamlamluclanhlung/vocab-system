"""The official operational runtime composition root frozen by D70.

This module never imports the concrete event journal module. It obtains a
journal only through ``vocab.runtime.eventlog_authority``, and only inside a
command that holds the deployment lock.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .anki import AnkiConnectClient
from .runtime.bootstrap import create_deployment, evaluate_preconditions
from .runtime.config import RuntimeConfig, load_config
from .runtime.errors import RuntimeLockError, VocabRuntimeError
from .runtime.layout import build_layout
from .runtime.preflight import run_standalone_preflight


EXIT_SUCCESS = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_LOCK_CONTENTION = 3
EXIT_ITEM_FAILURES = 4


def _build_anki(config: RuntimeConfig) -> AnkiConnectClient:
    return AnkiConnectClient(
        endpoint=config.anki.endpoint,
        timeout=config.anki.timeout,
    )


def _command_preflight(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    layout = build_layout(config.data_root)
    report = run_standalone_preflight(config, layout, _build_anki(config))
    print(f"deployment: {config.data_root}")
    print(report.render())
    if not report.ok:
        print(f"\npreflight FAILED: {len(report.failed)} check(s)")
        return EXIT_REFUSED
    print("\npreflight OK (diagnostic only; not authority to write)")
    return EXIT_SUCCESS


def _command_bootstrap(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)
    preconditions = evaluate_preconditions(
        config,
        anki,
        confirm_new_deployment=bool(arguments.confirm_new_deployment),
        confirm_clean_production_profile=bool(
            arguments.confirm_clean_production_profile
        ),
    )

    print(f"target data root: {config.data_root}")
    print(
        f"\nProduction profile registry — "
        f"{preconditions.registry_count} VocabularyUnit note(s):\n"
    )
    if preconditions.registry:
        width = max(len(entry.unit_key) for entry in preconditions.registry)
        for entry in preconditions.registry:
            print(f"  {entry.unit_key.ljust(width)}  {entry.lemma}")
    else:
        print("  (none)")
    print(f"\nregistry digest: {preconditions.registry_digest}")

    if not preconditions.confirmations_present:
        print(
            "\nThis registry will become the production registry. Nothing has "
            "been created.\nRe-run with --confirm-new-deployment and "
            "--confirm-clean-production-profile to proceed."
        )
        return EXIT_REFUSED

    result = create_deployment(preconditions)
    print(f"\nbootstrapped deployment {result.identity.runtime_id}")
    print(f"namespace durability (D-6): {result.namespace_durable}")
    return EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    """Build the frozen Wave A command surface."""
    parser = argparse.ArgumentParser(
        prog="vocab",
        description="Operational runtime for the vocabulary system (D70).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="create one new deployment data root",
    )
    bootstrap.add_argument("--config", required=True)
    bootstrap.add_argument(
        "--confirm-new-deployment",
        action="store_true",
        dest="confirm_new_deployment",
    )
    bootstrap.add_argument(
        "--confirm-clean-production-profile",
        action="store_true",
        dest="confirm_clean_production_profile",
    )
    bootstrap.set_defaults(handler=_command_bootstrap)

    preflight = subparsers.add_parser(
        "preflight",
        help="diagnose one deployment without locking or writing",
    )
    preflight.add_argument("--config", required=True)
    preflight.set_defaults(handler=_command_preflight)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command, mapping every refusal onto a frozen exit code.

    Only the runtime exception taxonomy is caught. Known operational failures
    from Anki, storage, and the core are normalized into that taxonomy at the
    runtime boundary. Anything outside it is a programming defect and is
    allowed to surface with a traceback, because a defect reported as exit 1
    would be indistinguishable from a deliberate fail-closed refusal.
    """
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except RuntimeLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_LOCK_CONTENTION
    except VocabRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
