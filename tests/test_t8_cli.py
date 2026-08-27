"""Thin offline tests for the T8.1 argparse/file orchestration boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

import vocab.t8_cli as cli
from vocab.context import ContextPreview
from vocab.context_batch import ContextImportResult, ContextOutcome
from vocab.hydrate import AudioOutcome
from vocab.tts import FROZEN_TTS_CONFIG


def preview() -> ContextPreview:
    return ContextPreview(
        unit_key="subtle::small-difference",
        lemma="subtle",
        definition_en="hard to notice",
        register="neutral",
        Ctx_1="one",
        Ctx_2="two",
        Ctx_3="three",
        Ctx_4="four",
        Ctx_5="five",
    )


def test_parser_exposes_exact_three_commands() -> None:
    parser = cli.build_parser()

    export = parser.parse_args(["export-contexts", "--out", "request.json"])
    imported = parser.parse_args(["import-contexts", "--in", "response.json"])
    audio = parser.parse_args(["hydrate-audio", "--note-id", "17"])

    assert export.command == "export-contexts"
    assert export.limit == 20
    assert imported.command == "import-contexts"
    assert audio.command == "hydrate-audio" and audio.note_id == 17


def test_export_writes_exact_deterministic_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = b'{"units":[{"unit_key":"safe"}]}'
    calls = []
    monkeypatch.setattr(
        cli,
        "export_context_batch",
        lambda **kwargs: calls.append(kwargs) or artifact,
    )
    output: list[str] = []
    path = tmp_path / "request.json"

    cli.export_contexts_to_file(
        path,
        anki="fake-anki",
        limit=7,
        output=output.append,
    )

    assert path.read_bytes() == artifact
    assert calls == [{"anki": "fake-anki", "limit": 7}]
    assert output == [f"exported context request: {path} (1 Units)"]


@pytest.mark.parametrize(
    ("answer", "accepted"),
    [("y", True), ("Y", True), ("", False), ("yes", False), (" y ", False)],
)
def test_import_only_exact_y_or_uppercase_y_confirms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answer: str,
    accepted: bool,
) -> None:
    path = tmp_path / "response.json"
    path.write_bytes(b"response")
    observed = []

    def fake_import(raw, *, anki, confirmation):
        observed.append((raw, anki, confirmation(preview())))
        outcome = ContextOutcome.CREATED if observed[-1][2] else ContextOutcome.DECLINED
        return (ContextImportResult("subtle::small-difference", outcome),)

    monkeypatch.setattr(cli, "import_context_response", fake_import)
    output: list[str] = []

    cli.import_contexts_from_file(
        path,
        anki="fake-anki",
        input_func=lambda _prompt: answer,
        output=output.append,
    )

    assert observed == [(b"response", "fake-anki", accepted)]
    assert any("Ctx_5: five" in line for line in output)
    assert output[-1].endswith("CREATED" if accepted else "DECLINED")


def test_hydrate_audio_command_constructs_frozen_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_synth = object()
    calls = []
    monkeypatch.setattr(cli, "KokoroLocalSynthesizer", lambda: fake_synth)
    monkeypatch.setattr(
        cli,
        "hydrate_audio",
        lambda note_id, **kwargs: calls.append((note_id, kwargs))
        or AudioOutcome.CREATED,
    )
    output: list[str] = []

    cli.hydrate_audio_note(17, anki="fake-anki", output=output.append)

    assert calls == [
        (
            17,
            {
                "anki": "fake-anki",
                "synthesizer": fake_synth,
                "tts_config": FROZEN_TTS_CONFIG,
            },
        )
    ]
    assert output == ["note 17: CREATED"]
