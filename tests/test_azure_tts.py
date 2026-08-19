"""Mocked-urllib tests for the stdlib Azure Speech REST adapter."""

from __future__ import annotations

import urllib.error
import xml.etree.ElementTree as ET

import pytest

import vocab.azure_tts as azure_module
from vocab.azure_tts import (
    AzureSpeechSynthesizer,
    AzureTtsConfigError,
    AzureTtsResponseError,
    AzureTtsTransportError,
)
from vocab.media_contract import AUDIO_OUTPUT_FORMAT
from vocab.media_contract import AUDIO_PROVIDER_ID


class FakeHTTPResponse:
    def __init__(self, body: object) -> None:
        self.body = body

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> object:
        return self.body


def test_synthesizer_exposes_read_only_d29_identity() -> None:
    synthesizer = AzureSpeechSynthesizer("eastus")

    assert synthesizer.provider_id == AUDIO_PROVIDER_ID
    assert synthesizer.region == "eastus"
    assert synthesizer.output_format == AUDIO_OUTPUT_FORMAT
    with pytest.raises(AttributeError):
        synthesizer.region = "southeastasia"


def install_response(
    monkeypatch: pytest.MonkeyPatch,
    body: object,
) -> list[tuple[object, float]]:
    calls: list[tuple[object, float]] = []

    def fake_urlopen(request, *, timeout):
        calls.append((request, timeout))
        return FakeHTTPResponse(body)

    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-speech-key")
    return calls


def test_exact_endpoint_headers_minimal_ssml_and_success(monkeypatch) -> None:
    calls = install_response(monkeypatch, b"mp3")
    synthesizer = AzureSpeechSynthesizer("southeastasia", timeout=8)

    result = synthesizer.synthesize(
        text="A subtle change became clear.",
        voice_id="en-US-Voice-One",
        locale="en-US",
    )

    assert result == b"mp3"
    request, timeout = calls[0]
    assert request.full_url == (
        "https://southeastasia.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    assert request.get_method() == "POST"
    assert request.headers["Ocp-apim-subscription-key"] == "test-speech-key"
    assert request.headers["Content-type"] == "application/ssml+xml"
    assert request.headers["X-microsoft-outputformat"] == AUDIO_OUTPUT_FORMAT
    assert request.headers["User-agent"] == "vocab-system"
    assert timeout == 8
    assert request.data.decode("utf-8") == (
        '<speak version="1.0" xml:lang="en-US">'
        '<voice name="en-US-Voice-One">'
        "A subtle change became clear."
        "</voice></speak>"
    )
    assert all(
        forbidden not in request.data.decode("utf-8")
        for forbidden in (
            "prosody",
            "emphasis",
            "phoneme",
            "break",
            "say-as",
            "style",
        )
    )


def test_ssml_escapes_text_and_attributes_without_rewriting(monkeypatch) -> None:
    calls = install_response(monkeypatch, b"x")
    text = 'Exact & text <with> "quotes".'
    voice = 'voice & <one> "quoted"'
    locale = 'en-&<"'

    AzureSpeechSynthesizer("eastus").synthesize(
        text=text,
        voice_id=voice,
        locale=locale,
    )

    root = ET.fromstring(calls[0][0].data.decode("utf-8"))
    voice_node = root.find("voice")
    assert root.attrib["{http://www.w3.org/XML/1998/namespace}lang"] == locale
    assert voice_node is not None
    assert voice_node.attrib["name"] == voice
    assert voice_node.text == text


@pytest.mark.parametrize("body", [b"", "not-bytes"])
def test_empty_or_non_bytes_response_fails(monkeypatch, body) -> None:
    install_response(monkeypatch, body)

    with pytest.raises(AzureTtsResponseError):
        AzureSpeechSynthesizer("eastus").synthesize(
            text="Short sentence.",
            voice_id="voice-one",
            locale="en-US",
        )


def test_missing_key_fails_before_http(monkeypatch) -> None:
    calls = install_response(monkeypatch, b"audio")
    monkeypatch.delenv("AZURE_SPEECH_KEY")

    with pytest.raises(AzureTtsConfigError, match="AZURE_SPEECH_KEY"):
        AzureSpeechSynthesizer("eastus").synthesize(
            text="Short sentence.",
            voice_id="voice-one",
            locale="en-US",
        )

    assert calls == []


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_http_failures_have_one_attempt_and_do_not_leak_key(
    monkeypatch,
    status: int,
) -> None:
    secret = "super-secret-speech-key"
    attempts = 0

    def fail_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            secret,
            hdrs=None,
            fp=None,
        )

    monkeypatch.setenv("AZURE_SPEECH_KEY", secret)
    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(AzureTtsTransportError) as captured:
        AzureSpeechSynthesizer("eastus").synthesize(
            text="Short sentence.",
            voice_id="voice-one",
            locale="en-US",
        )

    assert attempts == 1
    assert str(status) in str(captured.value)
    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("network secret"), TimeoutError("timeout secret")],
)
def test_network_failures_have_one_attempt_and_sanitized_message(
    monkeypatch,
    failure: BaseException,
) -> None:
    secret = "super-secret-speech-key"
    attempts = 0

    def fail_urlopen(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setenv("AZURE_SPEECH_KEY", secret)
    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(AzureTtsTransportError) as captured:
        AzureSpeechSynthesizer("eastus").synthesize(
            text="Short sentence.",
            voice_id="voice-one",
            locale="en-US",
        )

    assert attempts == 1
    assert secret not in str(captured.value)
    assert "secret" not in str(captured.value)
