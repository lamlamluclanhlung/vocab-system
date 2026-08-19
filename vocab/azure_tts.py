"""Stdlib-only Azure Speech REST adapter for offline T8 synthesis."""

from __future__ import annotations

import http.client
import math
import os
import re
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr

from .media_contract import AUDIO_OUTPUT_FORMAT, AUDIO_PROVIDER_ID


DEFAULT_AZURE_TTS_TIMEOUT = 60.0
_AZURE_REGION_RE = re.compile(r"^[A-Za-z0-9-]+$")


class AzureTtsError(RuntimeError):
    """Base class for Azure Speech synthesis failures."""


class AzureTtsConfigError(AzureTtsError):
    """Raised for missing or invalid explicit runtime configuration."""


class AzureTtsTransportError(AzureTtsError):
    """Raised when the single provider attempt cannot complete."""


class AzureTtsResponseError(AzureTtsError):
    """Raised when the provider returns an unusable audio body."""


class AzureSpeechSynthesizer:
    """One-attempt Azure Speech REST synthesizer for one configured region."""

    def __init__(
        self,
        region: str,
        *,
        timeout: float = DEFAULT_AZURE_TTS_TIMEOUT,
    ) -> None:
        if not isinstance(region, str):
            raise TypeError("region must be a string")
        if not region or region != region.strip():
            raise AzureTtsConfigError(
                "region must be non-empty without surrounding whitespace"
            )
        if _AZURE_REGION_RE.fullmatch(region) is None:
            raise AzureTtsConfigError("region contains invalid characters")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a finite positive number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        self._region = region
        self.timeout = float(timeout)

    @property
    def provider_id(self) -> str:
        """Return the exact provider identity used for synthesis."""
        return AUDIO_PROVIDER_ID

    @property
    def region(self) -> str:
        """Return the exact configured Azure region without normalization."""
        return self._region

    @property
    def output_format(self) -> str:
        """Return the exact Azure output format used for synthesis."""
        return AUDIO_OUTPUT_FORMAT

    def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        locale: str,
    ) -> bytes:
        """Synthesize exact text with minimal escaped SSML and one request."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if text == "":
            raise AzureTtsConfigError("text must be non-empty")
        _require_exact_nonempty("voice_id", voice_id)
        _require_exact_nonempty("locale", locale)

        speech_key = os.environ.get("AZURE_SPEECH_KEY")
        if not speech_key:
            raise AzureTtsConfigError("AZURE_SPEECH_KEY is required")

        ssml = (
            f'<speak version="1.0" xml:lang={quoteattr(locale)}>'
            f"<voice name={quoteattr(voice_id)}>"
            f"{escape(text)}"
            "</voice></speak>"
        )
        endpoint = (
            f"https://{self.region}.tts.speech.microsoft.com/"
            "cognitiveservices/v1"
        )
        request = urllib.request.Request(
            endpoint,
            data=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": speech_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": AUDIO_OUTPUT_FORMAT,
                "User-Agent": "vocab-system",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
            ) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            raise AzureTtsTransportError(
                f"Azure Speech request failed with HTTP status {exc.code}"
            ) from None
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ):
            raise AzureTtsTransportError(
                "Azure Speech request could not be completed"
            ) from None

        if not isinstance(audio, bytes):
            raise AzureTtsResponseError("Azure Speech response must be bytes")
        if not audio:
            raise AzureTtsResponseError(
                "Azure Speech response must contain non-empty audio"
            )
        return audio


def _require_exact_nonempty(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or not value.strip():
        raise AzureTtsConfigError(f"{name} must be non-empty")
    if value != value.strip():
        raise AzureTtsConfigError(
            f"{name} must not contain leading or trailing whitespace"
        )
