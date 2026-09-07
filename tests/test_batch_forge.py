"""Batch Forge convenience transport and CLI tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocab import batch_cli
from vocab.artifact_json import canonical_json_bytes
from vocab.forge.request import ForgeResult, ForgeStatus
from vocab.runtime import batch_forge, forge_bridge
from vocab.runtime.errors import RuntimeForgeBridgeError


TSV = (
    "subtle\tcorpus:user:doc-1\tThe difference was subtle but important.\n"
    "rule out\tcorpus:user:doc-2\tThe tests helped doctors rule out several possible causes.\tmedical reading\n"
).encode("utf-8")


def build_request_bytes() -> bytes:
    return batch_forge.build_batch_request_artifact(TSV, forge_bridge.load_prompt())


def response_bytes(request_raw: bytes | None = None) -> bytes:
    request = batch_forge.parse_batch_request_artifact(
        request_raw if request_raw is not None else build_request_bytes()
    )
    items = [
        {
            "generation_request_sha256": item.generation_request_sha256,
            "structured_output": {},
        }
        for item in request.requests
    ]
    return canonical_json_bytes(
        {
            "artifact": batch_forge.BATCH_RESPONSE_ARTIFACT,
            "artifact_version": 1,
            "source_batch_id": request.batch_id,
            "model_id": "OpenAI",
            "model_version": "GPT-5.6 Sol",
            "items": items,
        }
    )


def test_parse_batch_tsv_builds_only_human_supplied_requests() -> None:
    requests = batch_forge.parse_batch_tsv(TSV)
    assert len(requests) == 2
    assert requests[0].source_ref == "corpus:user:doc-1"
    assert requests[0].source_sentence == "The difference was subtle but important."
    assert requests[0].learner_note == "Target Unit: subtle"
    assert requests[1].learner_note == (
        "Target Unit: rule out\nLearner note: medical reading"
    )


def test_parse_batch_tsv_rejects_missing_source_ref_instead_of_inventing_one() -> None:
    with pytest.raises(RuntimeForgeBridgeError, match="3 or 4"):
        batch_forge.parse_batch_tsv(b"subtle\tThe difference was subtle.\n")


def test_parse_batch_tsv_rejects_duplicate_requests() -> None:
    row = b"subtle\tcorpus:user:doc-1\tThe difference was subtle.\n"
    with pytest.raises(RuntimeForgeBridgeError, match="duplicates"):
        batch_forge.parse_batch_tsv(row + row)


def test_parse_batch_tsv_rejects_more_than_twenty_items() -> None:
    rows = "".join(
        f"word{i}\tcorpus:user:doc-{i}\tThis sentence contains word{i} clearly.\n"
        for i in range(21)
    ).encode("utf-8")
    with pytest.raises(RuntimeForgeBridgeError, match="at most 20"):
        batch_forge.parse_batch_tsv(rows)


def test_parse_batch_tsv_rejects_bom_and_padded_identity_fields() -> None:
    with pytest.raises(RuntimeForgeBridgeError, match="BOM"):
        batch_forge.parse_batch_tsv(b"\xef\xbb\xbf" + TSV)
    with pytest.raises(RuntimeForgeBridgeError, match="leading or trailing"):
        batch_forge.parse_batch_tsv(
            b" subtle\tcorpus:user:doc-1\tThe difference was subtle.\n"
        )


def test_batch_request_is_deterministic_and_contains_standard_requests() -> None:
    first = build_request_bytes()
    second = build_request_bytes()
    assert first == second

    artifact = batch_forge.parse_batch_request_artifact(first)
    assert len(artifact.requests) == 2
    assert artifact.requests[0].request.source_ref == "corpus:user:doc-1"
    assert artifact.requests[1].request.source_ref == "corpus:user:doc-2"
    assert all(item.prompt_version == "forge-v1" for item in artifact.requests)


def test_batch_request_rejects_edited_instructions_and_batch_id() -> None:
    body = json.loads(build_request_bytes())
    body["instructions"] = "edited"
    with pytest.raises(RuntimeForgeBridgeError, match="instructions"):
        batch_forge.parse_batch_request_artifact(canonical_json_bytes(body))

    body = json.loads(build_request_bytes())
    body["batch_id"] = "a" * 64
    with pytest.raises(RuntimeForgeBridgeError, match="does not match"):
        batch_forge.parse_batch_request_artifact(canonical_json_bytes(body))


def test_batch_response_round_trips_into_standard_forge_responses() -> None:
    request_raw = build_request_bytes()
    request = batch_forge.parse_batch_request_artifact(request_raw)
    response = batch_forge.parse_batch_response_artifact(response_bytes(request_raw))

    assert response.source_batch_id == request.batch_id
    assert [item.generation_request_sha256 for item in response.responses] == [
        item.generation_request_sha256 for item in request.requests
    ]
    assert all(item.model_id == "OpenAI" for item in response.responses)
    assert all(item.model_version == "GPT-5.6 Sol" for item in response.responses)


def test_batch_response_rejects_reordering_unknown_keys_and_blank_model() -> None:
    body = json.loads(response_bytes())
    body["items"] = list(reversed(body["items"]))
    with pytest.raises(RuntimeForgeBridgeError, match="source_batch_id"):
        batch_forge.parse_batch_response_artifact(canonical_json_bytes(body))

    body = json.loads(response_bytes())
    body["extra"] = 1
    with pytest.raises(RuntimeForgeBridgeError, match="unknown keys"):
        batch_forge.parse_batch_response_artifact(canonical_json_bytes(body))

    body = json.loads(response_bytes())
    body["model_id"] = ""
    with pytest.raises(RuntimeForgeBridgeError, match="model_id"):
        batch_forge.parse_batch_response_artifact(canonical_json_bytes(body))


def test_bind_batch_binds_every_item_before_replay() -> None:
    prompt = forge_bridge.load_prompt()
    request = batch_forge.parse_batch_request_artifact(build_request_bytes())
    response = batch_forge.parse_batch_response_artifact(response_bytes())
    bound = batch_forge.bind_batch(request, response, prompt)

    assert len(bound) == 2
    assert [item.request for item in bound] == [
        item.request for item in request.requests
    ]
    assert all(item.metadata.model_id == "OpenAI" for item in bound)


def test_batch_export_cli_writes_exclusively(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "words.tsv"
    output_path = tmp_path / "batch-request.json"
    input_path.write_bytes(TSV)

    assert (
        batch_cli.main(
            [
                "batch-forge-export",
                "--input",
                str(input_path),
                "--out",
                str(output_path),
            ]
        )
        == 0
    )
    assert output_path.exists()
    assert "2 requests" in capsys.readouterr().out

    assert (
        batch_cli.main(
            [
                "batch-forge-export",
                "--input",
                str(input_path),
                "--out",
                str(output_path),
            ]
        )
        == 1
    )


def test_batch_import_reads_artifacts_only_after_write_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"inside": False}
    dummy_config = SimpleNamespace()
    dummy_anki = object()

    @contextmanager
    def fake_write_operation(config, anki):
        assert config is dummy_config
        assert anki is dummy_anki
        state["inside"] = True
        try:
            yield SimpleNamespace(event_log_path=Path("events.jsonl"))
        finally:
            state["inside"] = False

    def fail_read(path: Path, label: str) -> bytes:
        assert state["inside"] is True
        raise RuntimeForgeBridgeError("stop after authority")

    monkeypatch.setattr(batch_cli, "load_config", lambda path: dummy_config)
    monkeypatch.setattr(batch_cli, "_build_anki", lambda config: dummy_anki)
    monkeypatch.setattr(batch_cli, "write_operation", fake_write_operation)
    monkeypatch.setattr(batch_cli, "_read_file", fail_read)

    code = batch_cli.main(
        [
            "batch-forge-import",
            "--config",
            "runtime.json",
            "--request",
            "request.json",
            "--response",
            "response.json",
            "--actor-id",
            "hai",
        ]
    )
    assert code == 1
    assert state["inside"] is False


def test_batch_import_processes_all_bound_items_and_summarizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    request_path = tmp_path / "batch-request.json"
    response_path = tmp_path / "batch-response.json"
    request_path.write_bytes(build_request_bytes())
    response_path.write_bytes(response_bytes(request_path.read_bytes()))

    dummy_config = SimpleNamespace(
        anki=SimpleNamespace(deck_name="Vocabulary")
    )
    dummy_anki = object()

    @contextmanager
    def fake_write_operation(config, anki):
        yield SimpleNamespace(event_log_path=tmp_path / "events.jsonl")

    seen = []

    def fake_forge(request, **kwargs):
        seen.append(request)
        index = len(seen)
        return ForgeResult(
            status=ForgeStatus.CREATED,
            unit_key=f"unit-{index}::sense",
            note_id=1000 + index,
        )

    monkeypatch.setattr(batch_cli, "load_config", lambda path: dummy_config)
    monkeypatch.setattr(batch_cli, "_build_anki", lambda config: dummy_anki)
    monkeypatch.setattr(batch_cli, "write_operation", fake_write_operation)
    monkeypatch.setattr(batch_cli, "open_runtime_event_log", lambda path: object())
    monkeypatch.setattr(batch_cli, "forge", fake_forge)

    code = batch_cli.main(
        [
            "batch-forge-import",
            "--config",
            "runtime.json",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--actor-id",
            "hai",
        ]
    )

    assert code == 0
    assert len(seen) == 2
    output = capsys.readouterr().out
    assert "total=2  failed=0" in output
    assert "batch forge OK" in output
