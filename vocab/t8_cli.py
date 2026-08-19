"""Thin command-line interface for human-mediated T8.1 workflows."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from .anki import AnkiConnectClient
from .context import ContextPreview
from .context_batch import (
    DEFAULT_CONTEXT_BATCH_SIZE,
    export_context_batch,
    import_context_response,
)
from .hydrate import hydrate_audio
from .kokoro_tts import KokoroLocalSynthesizer
from .tts import FROZEN_TTS_CONFIG


Output = Callable[[str], None]
Input = Callable[[str], str]


def export_contexts_to_file(
    path: Path,
    *,
    anki: AnkiConnectClient,
    limit: int = DEFAULT_CONTEXT_BATCH_SIZE,
    output: Output = print,
) -> None:
    artifact = export_context_batch(anki=anki, limit=limit)
    path.write_bytes(artifact)
    output(f"exported context request: {path} ({_artifact_unit_count(artifact)} Units)")


def import_contexts_from_file(
    path: Path,
    *,
    anki: AnkiConnectClient,
    input_func: Input = input,
    output: Output = print,
) -> None:
    def confirm(preview: ContextPreview) -> bool:
        output(_format_preview(preview))
        return input_func("Accept? [y/N] ") in ("y", "Y")

    results = import_context_response(
        path.read_bytes(),
        anki=anki,
        confirmation=confirm,
    )
    for result in results:
        suffix = ""
        if result.violations:
            suffix = " " + ",".join(result.violations)
        output(f"{result.unit_key}: {result.outcome.value}{suffix}")


def hydrate_audio_note(
    note_id: int,
    *,
    anki: AnkiConnectClient,
    output: Output = print,
) -> None:
    outcome = hydrate_audio(
        note_id,
        anki=anki,
        synthesizer=KokoroLocalSynthesizer(),
        tts_config=FROZEN_TTS_CONFIG,
    )
    output(f"note {note_id}: {outcome.value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vocab.t8_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-contexts")
    export_parser.add_argument("--out", required=True, type=Path)
    export_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_CONTEXT_BATCH_SIZE,
    )

    import_parser = subparsers.add_parser("import-contexts")
    import_parser.add_argument("--in", required=True, dest="input_path", type=Path)

    audio_parser = subparsers.add_parser("hydrate-audio")
    audio_parser.add_argument("--note-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    anki = AnkiConnectClient()
    if args.command == "export-contexts":
        export_contexts_to_file(args.out, anki=anki, limit=args.limit)
    elif args.command == "import-contexts":
        import_contexts_from_file(args.input_path, anki=anki)
    elif args.command == "hydrate-audio":
        hydrate_audio_note(args.note_id, anki=anki)
    else:  # pragma: no cover - argparse enforces the command inventory
        raise AssertionError(f"unhandled command: {args.command}")
    return 0


def _format_preview(preview: ContextPreview) -> str:
    lines = [
        f"Unit: {preview.unit_key}",
        f"Lemma: {preview.lemma}",
        f"Definition: {preview.definition_en}",
        f"Register: {preview.register}",
    ]
    for index in range(1, 6):
        lines.append(f"Ctx_{index}: {getattr(preview, f'Ctx_{index}')}")
    return "\n".join(lines)


def _artifact_unit_count(artifact: bytes) -> int:
    # The artifact was just created by the deterministic exporter. Avoid
    # duplicating transport validation solely for a safe CLI count.
    import json

    value = json.loads(artifact.decode("utf-8"))
    return len(value["units"])


if __name__ == "__main__":
    raise SystemExit(main())
