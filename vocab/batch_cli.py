"""Convenience CLI for human-mediated batch Forge orchestration.

The official one-Unit Forge core and bridge remain unchanged. This CLI only
packages multiple standard request artifacts and replays already-bound items
through the same forge() path under the same deployment write authority.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from .anki import AnkiConnectClient
from .forge import forge
from .forge.request import ForgeStatus
from .runtime import batch_forge, forge_bridge
from .runtime.config import RuntimeConfig, load_config
from .runtime.errors import RuntimeForgeBridgeError, RuntimeLockError, VocabRuntimeError
from .runtime.eventlog_authority import open_runtime_event_log
from .runtime.normalize import FILESYSTEM_SEAM, normalized
from .runtime.operation import write_operation


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


def _read_file(path: Path, label: str) -> bytes:
    with normalized(
        RuntimeForgeBridgeError,
        f"{label} could not be read",
        catching=FILESYSTEM_SEAM,
    ):
        return path.read_bytes()


def _write_exclusive(path: Path, body: bytes, label: str) -> None:
    with normalized(
        RuntimeForgeBridgeError,
        f"{label} could not be written",
        catching=FILESYSTEM_SEAM,
    ):
        with path.open("xb") as handle:
            handle.write(body)


def _render_result(result) -> str:
    line = f"{result.status.value}  unit_key={result.unit_key or '-'}"
    if result.outcome:
        line += f"  outcome={result.outcome}"
    if result.note_id is not None:
        line += f"  note={result.note_id}"
    if result.violations:
        line += f"  violations={','.join(result.violations)}"
    return line


def _command_batch_forge_export(arguments: argparse.Namespace) -> int:
    tsv_raw = _read_file(Path(arguments.input), "batch TSV")
    prompt = forge_bridge.load_prompt()
    body = batch_forge.build_batch_request_artifact(tsv_raw, prompt)
    artifact = batch_forge.parse_batch_request_artifact(body)
    out = Path(arguments.out)
    _write_exclusive(out, body, "batch request artifact")

    print(f"wrote {out} ({len(artifact.requests)} requests)")
    print(f"batch_id {artifact.batch_id}")
    print(f"prompt {prompt.version} {prompt.sha256}")
    return EXIT_SUCCESS


def _command_batch_forge_import(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    # Match D70 section 11 ordering: deployment identity, lock, full write
    # preflight, then operation-specific artifact reads and binding.
    with write_operation(config, anki) as layout:
        prompt = forge_bridge.load_prompt()
        request_artifact = batch_forge.parse_batch_request_artifact(
            _read_file(Path(arguments.request), "batch request artifact")
        )
        response_artifact = batch_forge.parse_batch_response_artifact(
            _read_file(Path(arguments.response), "batch response artifact")
        )
        bound_items = batch_forge.bind_batch(
            request_artifact, response_artifact, prompt
        )

        confirmation = forge_bridge.TerminalConfirmation(
            arguments.actor_id,
            stream_in=sys.stdin,
            stream_out=sys.stdout,
        )
        journal = open_runtime_event_log(layout.event_log_path)

        failed = 0
        for bound in bound_items:
            result = forge(
                bound.request,
                deck_name=config.anki.deck_name,
                generator=forge_bridge.ReplayGenerator(bound),
                anki=anki,
                event_log=journal,
                confirmation=confirmation,
                generation_metadata=bound.metadata,
                today=forge_bridge.local_day,
                attempt_id_factory=lambda: uuid.uuid4().hex,
            )
            print(_render_result(result))
            if result.status is not ForgeStatus.CREATED:
                failed += 1

    print(f"\ntotal={len(bound_items)}  failed={failed}")
    if failed == 0:
        print("batch forge OK")
        return EXIT_SUCCESS
    return EXIT_ITEM_FAILURES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vocab-batch",
        description="Batch convenience wrapper around the frozen Forge bridge.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser(
        "batch-forge-export",
        help="package 1-20 TSV rows into one manual-generation batch request",
    )
    export.add_argument("--input", required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(handler=_command_batch_forge_export)

    import_command = subparsers.add_parser(
        "batch-forge-import",
        help="bind and replay one saved batch response through Forge",
    )
    import_command.add_argument("--config", required=True)
    import_command.add_argument("--request", required=True)
    import_command.add_argument("--response", required=True)
    import_command.add_argument("--actor-id", required=True, dest="actor_id")
    import_command.set_defaults(handler=_command_batch_forge_import)

    return parser


def main(argv: list[str] | None = None) -> int:
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
