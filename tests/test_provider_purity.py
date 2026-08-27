"""Mechanical guard against paid-provider or reserved-audio T8 regressions."""

from __future__ import annotations

from pathlib import Path

from vocab.media_contract import (
    ACTIVE_AUDIO_SLOT,
    AUDIO_PROVIDER_ID,
    NORMAL_REVIEW_AUDIO_FIELD,
    RESERVED_AUDIO_FIELDS,
)


ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "vocab"


def test_active_python_contains_no_paid_provider_implementation_tokens() -> None:
    forbidden = (
        "OPENAI_API_KEY",
        "AZURE_SPEECH_KEY",
        "api.openai.com",
        "api.anthropic.com",
        "cognitiveservices",
        "AzureSpeechSynthesizer",
        "OpenAIContextGenerator",
        "azure-speech-rest",
    )
    occurrences = []
    for path in VOCAB.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                occurrences.append((path.relative_to(ROOT).as_posix(), token))

    assert occurrences == []


def test_active_media_contract_has_single_listening_artifact() -> None:
    assert NORMAL_REVIEW_AUDIO_FIELD == "audio_1"
    assert RESERVED_AUDIO_FIELDS == ("audio_2", "audio_3")
    assert ACTIVE_AUDIO_SLOT == 1
    assert AUDIO_PROVIDER_ID == "kokoro-local"


def test_t8_runtime_modules_never_reference_reserved_audio_fields() -> None:
    for relative in (
        "context_batch.py",
        "hydrate.py",
        "kokoro_tts.py",
        "t8_cli.py",
        "tts.py",
    ):
        source = (VOCAB / relative).read_text(encoding="utf-8")
        assert "audio_2" not in source
        assert "audio_3" not in source


def test_region_is_absent_from_active_tts_contract_and_runtime() -> None:
    for relative in ("hydrate.py", "kokoro_tts.py", "tts.py"):
        source = (VOCAB / relative).read_text(encoding="utf-8").lower()
        assert "region" not in source


def test_paid_provider_adapter_modules_are_deleted() -> None:
    assert not (VOCAB / "openai_context.py").exists()
    assert not (VOCAB / "azure_tts.py").exists()
