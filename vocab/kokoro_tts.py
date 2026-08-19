"""Lazy local Kokoro-to-MP3 adapter for offline T8 audio_1 synthesis."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media_contract import (
    AUDIO_BIT_RATE_KBPS,
    AUDIO_CHANNELS,
    AUDIO_ENCODER_QUALITY,
    AUDIO_ENCODER_VERSION,
    AUDIO_SAMPLE_RATE,
    KOKORO_CONFIG_FILENAME,
    KOKORO_INFERENCE_DEVICE,
    KOKORO_LANG_CODE,
    KOKORO_MODEL_FILENAME,
    KOKORO_MODEL_ID,
    KOKORO_MODEL_REVISION,
    KOKORO_MODEL_SHA256,
    KOKORO_PACKAGE_VERSION,
    KOKORO_SPEED,
    KOKORO_VOICE_FILENAME,
    KOKORO_VOICE_ID,
    KOKORO_VOICE_SHA256,
)
from .tts import FROZEN_TTS_CONFIG, TtsConfig


class KokoroTtsError(RuntimeError):
    """Base class for local Kokoro synthesis failures."""


class KokoroDependencyConfigError(KokoroTtsError):
    """Raised when pinned local dependencies or runtime config are invalid."""


class KokoroAssetError(KokoroTtsError):
    """Raised when pinned model assets cannot be acquired or verified."""


class KokoroSynthesisError(KokoroTtsError):
    """Raised when Kokoro cannot produce a valid finite mono waveform."""


class KokoroEncodingError(KokoroTtsError):
    """Raised when the pinned local MP3 encoder cannot produce bytes."""


@dataclass(frozen=True, slots=True)
class _Runtime:
    KModel: Any
    KPipeline: Any
    hf_hub_download: Any
    torch: Any
    lameenc: Any


class KokoroLocalSynthesizer:
    """One exact CPU-only Kokoro configuration with immutable D32 identity."""

    __slots__ = ()

    @property
    def synthesis_identity(self) -> TtsConfig:
        return FROZEN_TTS_CONFIG

    def synthesize(self, *, text: str) -> bytes:
        """Synthesize exact text locally and encode it as pinned MP3."""
        if type(text) is not str:
            raise TypeError("text must be a string")
        if text == "":
            raise KokoroDependencyConfigError("text must be non-empty")

        _require_package_versions()
        runtime = _load_runtime()
        config_path, model_path, voice_path = _acquire_verified_assets(runtime)
        pipeline = _build_pipeline(
            runtime,
            config_path=config_path,
            model_path=model_path,
            voice_path=voice_path,
        )
        samples = _synthesize_samples(pipeline, text)
        pcm = waveform_to_pcm_s16le(samples)
        return _encode_mp3(runtime, pcm)


def waveform_to_pcm_s16le(samples: Iterable[object]) -> bytes:
    """Encode finite mono samples using clipped symmetric half-away rounding."""
    if isinstance(samples, (str, bytes, bytearray)) or not isinstance(
        samples, Iterable
    ):
        raise KokoroSynthesisError("waveform samples must be an iterable")

    pcm = bytearray()
    count = 0
    for sample in samples:
        if isinstance(sample, bool) or not isinstance(sample, (int, float)):
            raise KokoroSynthesisError("waveform samples must be real numbers")
        numeric = float(sample)
        if not math.isfinite(numeric):
            raise KokoroSynthesisError("waveform contains NaN or infinity")
        clipped = min(1.0, max(-1.0, numeric))
        scaled = clipped * 32_767.0
        if scaled >= 0:
            integer = math.floor(scaled + 0.5)
        else:
            integer = math.ceil(scaled - 0.5)
        pcm.extend(struct.pack("<h", integer))
        count += 1

    if count == 0:
        raise KokoroSynthesisError("waveform must contain at least one sample")
    return bytes(pcm)


def _require_package_versions() -> None:
    for package, expected in (
        ("kokoro", KOKORO_PACKAGE_VERSION),
        ("lameenc", AUDIO_ENCODER_VERSION),
    ):
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise KokoroDependencyConfigError(
                f"required package is not installed: {package}"
            ) from None
        if actual != expected:
            raise KokoroDependencyConfigError(
                f"{package} version must be exactly {expected}; found {actual}"
            )


def _load_runtime() -> _Runtime:
    """Import all heavy/local-TTS dependencies only for real synthesis."""
    try:
        kokoro = importlib.import_module("kokoro")
        huggingface_hub = importlib.import_module("huggingface_hub")
        torch = importlib.import_module("torch")
        lameenc = importlib.import_module("lameenc")
        return _Runtime(
            KModel=kokoro.KModel,
            KPipeline=kokoro.KPipeline,
            hf_hub_download=huggingface_hub.hf_hub_download,
            torch=torch,
            lameenc=lameenc,
        )
    except (ImportError, AttributeError) as exc:
        raise KokoroDependencyConfigError(
            "pinned local TTS dependencies are unavailable or malformed"
        ) from exc


def _acquire_verified_assets(
    runtime: _Runtime,
) -> tuple[Path, Path, Path]:
    paths: list[Path] = []
    for filename in (
        KOKORO_CONFIG_FILENAME,
        KOKORO_MODEL_FILENAME,
        KOKORO_VOICE_FILENAME,
    ):
        try:
            downloaded = runtime.hf_hub_download(
                repo_id=KOKORO_MODEL_ID,
                revision=KOKORO_MODEL_REVISION,
                filename=filename,
            )
            path = Path(downloaded)
        except Exception as exc:
            raise KokoroAssetError(
                f"could not acquire pinned Kokoro asset: {filename}"
            ) from exc
        if not path.is_file():
            raise KokoroAssetError(
                f"pinned Kokoro asset is not a local file: {filename}"
            )
        paths.append(path)

    config_path, model_path, voice_path = paths
    _require_sha256(model_path, KOKORO_MODEL_SHA256, "model")
    _require_sha256(voice_path, KOKORO_VOICE_SHA256, "voice")
    return config_path, model_path, voice_path


def _require_sha256(path: Path, expected: str, label: str) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise KokoroAssetError(f"could not read pinned {label} asset") from exc
    if digest.hexdigest() != expected:
        raise KokoroAssetError(f"pinned {label} asset SHA256 mismatch")


def _build_pipeline(
    runtime: _Runtime,
    *,
    config_path: Path,
    model_path: Path,
    voice_path: Path,
) -> Any:
    try:
        model = runtime.KModel(
            repo_id=KOKORO_MODEL_ID,
            config=str(config_path),
            model=str(model_path),
        )
        model = model.to(KOKORO_INFERENCE_DEVICE).eval()
        pipeline = runtime.KPipeline(
            lang_code=KOKORO_LANG_CODE,
            repo_id=KOKORO_MODEL_ID,
            model=model,
            device=KOKORO_INFERENCE_DEVICE,
        )
        voice = runtime.torch.load(
            str(voice_path),
            map_location=KOKORO_INFERENCE_DEVICE,
            weights_only=True,
        )
        if not isinstance(getattr(pipeline, "voices", None), dict):
            raise TypeError("KPipeline voices cache is unavailable")
        pipeline.voices[KOKORO_VOICE_ID] = voice
        return pipeline
    except Exception as exc:
        raise KokoroAssetError(
            "could not construct Kokoro from the verified local assets"
        ) from exc


def _synthesize_samples(pipeline: Any, text: str) -> list[object]:
    samples: list[object] = []
    try:
        chunks = pipeline(
            text,
            voice=KOKORO_VOICE_ID,
            speed=KOKORO_SPEED,
        )
        for chunk in chunks:
            audio = getattr(chunk, "audio", None)
            samples.extend(_mono_chunk_samples(audio))
    except KokoroSynthesisError:
        raise
    except Exception as exc:
        raise KokoroSynthesisError("Kokoro synthesis failed") from exc
    if not samples:
        raise KokoroSynthesisError("Kokoro produced no audio samples")
    return samples


def _mono_chunk_samples(audio: object) -> list[object]:
    if audio is None:
        raise KokoroSynthesisError("Kokoro yielded a chunk without audio")
    value = audio
    for method_name in ("detach", "cpu"):
        method = getattr(value, method_name, None)
        if callable(method):
            value = method()

    shape_value = getattr(value, "shape", None)
    shape: tuple[int, ...] | None = None
    if shape_value is not None:
        try:
            shape = tuple(int(dimension) for dimension in shape_value)
        except (TypeError, ValueError):
            raise KokoroSynthesisError("waveform shape is malformed") from None
        if len(shape) == 1:
            pass
        elif len(shape) == 2 and shape[0] == 1:
            pass
        else:
            raise KokoroSynthesisError("waveform must be mono")

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    if not isinstance(value, (list, tuple)):
        raise KokoroSynthesisError("waveform data is malformed")
    if shape is not None and len(shape) == 2:
        if len(value) != 1 or not isinstance(value[0], (list, tuple)):
            raise KokoroSynthesisError("mono waveform shape does not match data")
        value = value[0]
    if any(isinstance(sample, (list, tuple)) for sample in value):
        raise KokoroSynthesisError("waveform must contain one mono sample stream")
    return list(value)


def _encode_mp3(runtime: _Runtime, pcm: bytes) -> bytes:
    try:
        encoder = runtime.lameenc.Encoder()
        encoder.set_bit_rate(AUDIO_BIT_RATE_KBPS)
        encoder.set_in_sample_rate(AUDIO_SAMPLE_RATE)
        encoder.set_channels(AUDIO_CHANNELS)
        encoder.set_quality(AUDIO_ENCODER_QUALITY)
        encoded = encoder.encode(pcm)
        flushed = encoder.flush()
    except Exception as exc:
        raise KokoroEncodingError("local MP3 encoding failed") from exc
    if not isinstance(encoded, bytes) or not isinstance(flushed, bytes):
        raise KokoroEncodingError("MP3 encoder must return bytes")
    result = encoded + flushed
    if not result:
        raise KokoroEncodingError("MP3 encoder returned empty output")
    return result
