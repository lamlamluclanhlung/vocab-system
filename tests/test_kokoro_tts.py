"""Offline fake-only tests for the pinned local Kokoro adapter."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

import vocab.kokoro_tts as kokoro_module
from vocab.kokoro_tts import (
    KokoroAssetError,
    KokoroDependencyConfigError,
    KokoroEncodingError,
    KokoroLocalSynthesizer,
    KokoroSynthesisError,
    waveform_to_pcm_s16le,
)
from vocab.tts import FROZEN_TTS_CONFIG


class FakeModel:
    def __init__(self, calls: dict[str, object], **kwargs: object) -> None:
        calls["model_kwargs"] = kwargs
        self.calls = calls

    def to(self, device: str) -> FakeModel:
        self.calls["model_device"] = device
        return self

    def eval(self) -> FakeModel:
        self.calls["model_eval"] = True
        return self


class FakeEncoder:
    def __init__(
        self,
        calls: dict[str, object],
        *,
        encoded: object,
        flushed: object,
    ) -> None:
        self.calls = calls
        self.encoded = encoded
        self.flushed = flushed

    def set_bit_rate(self, value: int) -> None:
        self.calls["bit_rate"] = value

    def set_in_sample_rate(self, value: int) -> None:
        self.calls["sample_rate"] = value

    def set_channels(self, value: int) -> None:
        self.calls["channels"] = value

    def set_quality(self, value: int) -> None:
        self.calls["quality"] = value

    def encode(self, pcm: bytes) -> object:
        self.calls["pcm"] = pcm
        return self.encoded

    def flush(self) -> object:
        return self.flushed


def install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    chunk_samples: list[list[float]] | None = None,
    encoded: object = b"mp3",
    flushed: object = b"tail",
) -> dict[str, object]:
    calls: dict[str, object] = {"downloads": [], "pipeline_calls": []}
    config_path = tmp_path / "config.json"
    model_path = tmp_path / "kokoro-v1_0.pth"
    voice_path = tmp_path / "voices" / "af_heart.pt"
    voice_path.parent.mkdir()
    config_path.write_bytes(b"config")
    model_path.write_bytes(b"model")
    voice_path.write_bytes(b"voice")
    paths = {
        "config.json": config_path,
        "kokoro-v1_0.pth": model_path,
        "voices/af_heart.pt": voice_path,
    }

    def download(**kwargs: object) -> str:
        calls["downloads"].append(kwargs)
        return str(paths[kwargs["filename"]])

    samples = chunk_samples if chunk_samples is not None else [[0.0, 0.5]]

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            calls["pipeline_kwargs"] = kwargs
            self.voices: dict[str, object] = {}

        def __call__(self, text: str, **kwargs: object):
            calls["pipeline_calls"].append((text, kwargs, dict(self.voices)))
            return [SimpleNamespace(audio=chunk) for chunk in samples]

    runtime = kokoro_module._Runtime(
        KModel=lambda **kwargs: FakeModel(calls, **kwargs),
        KPipeline=FakePipeline,
        hf_hub_download=download,
        torch=SimpleNamespace(
            load=lambda *args, **kwargs: calls.setdefault(
                "voice_load", (args, kwargs)
            )
        ),
        lameenc=SimpleNamespace(
            Encoder=lambda: FakeEncoder(
                calls,
                encoded=encoded,
                flushed=flushed,
            )
        ),
    )
    monkeypatch.setattr(kokoro_module, "_load_runtime", lambda: runtime)
    monkeypatch.setattr(
        kokoro_module.importlib.metadata,
        "version",
        lambda package: {"kokoro": "0.9.4", "lameenc": "1.8.4"}[package],
    )
    monkeypatch.setattr(
        kokoro_module,
        "KOKORO_MODEL_SHA256",
        hashlib.sha256(b"model").hexdigest(),
    )
    monkeypatch.setattr(
        kokoro_module,
        "KOKORO_VOICE_SHA256",
        hashlib.sha256(b"voice").hexdigest(),
    )
    return calls


def test_import_and_identity_need_no_heavy_dependencies() -> None:
    synthesizer = KokoroLocalSynthesizer()

    assert synthesizer.synthesis_identity is FROZEN_TTS_CONFIG
    assert synthesizer.synthesis_identity.provider == "kokoro-local"
    assert synthesizer.synthesis_identity.model_sha256
    assert synthesizer.synthesis_identity.voice_sha256
    assert synthesizer.synthesis_identity.encoder_id == "lameenc"
    assert synthesizer.synthesis_identity.output_format == (
        "mp3-48kbps-24khz-mono-s16le"
    )
    with pytest.raises(AttributeError):
        synthesizer.synthesis_identity = FROZEN_TTS_CONFIG


@pytest.mark.parametrize(
    ("package", "actual"),
    [("kokoro", "0.9.3"), ("lameenc", "1.8.3")],
)
def test_wrong_dependency_version_fails_before_runtime_or_synthesis(
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    actual: str,
) -> None:
    versions = {"kokoro": "0.9.4", "lameenc": "1.8.4"}
    versions[package] = actual
    monkeypatch.setattr(
        kokoro_module.importlib.metadata,
        "version",
        lambda name: versions[name],
    )
    monkeypatch.setattr(
        kokoro_module,
        "_load_runtime",
        lambda: pytest.fail("runtime import must not occur"),
    )

    with pytest.raises(KokoroDependencyConfigError, match=package):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")


def test_exact_assets_runtime_and_encoder_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = install_runtime(
        monkeypatch,
        tmp_path,
        chunk_samples=[[0.0, 0.5], [-0.5, 1.5]],
    )

    result = KokoroLocalSynthesizer().synthesize(text="  Exact text.  ")

    assert result == b"mp3tail"
    assert calls["downloads"] == [
        {
            "repo_id": "hexgrad/Kokoro-82M",
            "revision": "8542409da2986c0ab5d41b3cf0411f7a58caab38",
            "filename": filename,
        }
        for filename in (
            "config.json",
            "kokoro-v1_0.pth",
            "voices/af_heart.pt",
        )
    ]
    assert calls["model_kwargs"]["repo_id"] == "hexgrad/Kokoro-82M"
    assert calls["model_device"] == "cpu"
    assert calls["model_eval"] is True
    assert calls["pipeline_kwargs"]["lang_code"] == "a"
    assert calls["pipeline_kwargs"]["repo_id"] == "hexgrad/Kokoro-82M"
    assert calls["pipeline_kwargs"]["device"] == "cpu"
    assert calls["pipeline_calls"][0][0] == "  Exact text.  "
    assert calls["pipeline_calls"][0][1] == {"voice": "af_heart", "speed": 1.0}
    assert "af_heart" in calls["pipeline_calls"][0][2]
    assert calls["bit_rate"] == 48
    assert calls["sample_rate"] == 24000
    assert calls["channels"] == 1
    assert calls["quality"] == 2
    assert struct.unpack("<hhhh", calls["pcm"]) == (0, 16384, -16384, 32767)


def test_model_hash_mismatch_fails_before_model_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = install_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(kokoro_module, "KOKORO_MODEL_SHA256", "0" * 64)

    with pytest.raises(KokoroAssetError, match="model.*SHA256"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")

    assert "model_kwargs" not in calls


def test_voice_hash_mismatch_fails_before_model_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = install_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(kokoro_module, "KOKORO_VOICE_SHA256", "0" * 64)

    with pytest.raises(KokoroAssetError, match="voice.*SHA256"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")

    assert "model_kwargs" not in calls


def test_empty_kokoro_output_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_runtime(monkeypatch, tmp_path, chunk_samples=[])

    with pytest.raises(KokoroSynthesisError, match="no audio"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nan_or_infinite_waveform_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: float,
) -> None:
    install_runtime(monkeypatch, tmp_path, chunk_samples=[[0.0, invalid]])

    with pytest.raises(KokoroSynthesisError, match="NaN or infinity"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")


def test_empty_encoder_output_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_runtime(monkeypatch, tmp_path, encoded=b"", flushed=b"")

    with pytest.raises(KokoroEncodingError, match="empty"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")


def test_bytearray_encoder_output_is_returned_as_immutable_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_runtime(
        monkeypatch,
        tmp_path,
        encoded=bytearray(b"mp3"),
        flushed=bytearray(b"tail"),
    )

    result = KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")

    assert type(result) is bytes
    assert result == b"mp3tail"


@pytest.mark.parametrize(
    ("encoded", "flushed"),
    [
        (memoryview(b"mp3"), b"tail"),
        (b"mp3", memoryview(b"tail")),
    ],
)
def test_unsupported_encoder_output_type_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    encoded: object,
    flushed: object,
) -> None:
    install_runtime(monkeypatch, tmp_path, encoded=encoded, flushed=flushed)

    with pytest.raises(KokoroEncodingError, match="bytes or bytearray"):
        KokoroLocalSynthesizer().synthesize(text="Exact persisted text.")


def test_pcm_conversion_clips_and_uses_explicit_little_endian_rounding() -> None:
    pcm = waveform_to_pcm_s16le(
        [-2.0, -0.5, -0.5 / 32767, 0.0, 0.5 / 32767, 0.5, 2.0]
    )

    assert struct.unpack("<hhhhhhh", pcm) == (
        -32767,
        -16384,
        -1,
        0,
        1,
        16384,
        32767,
    )
