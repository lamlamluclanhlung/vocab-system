"""Provider-neutral ports consumed by the Forge core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ..models import Event, VocabUnit
from .request import (
    ConfirmationDecision,
    ForgePreview,
    ForgeRequest,
    GenerationMetadata,
)


class Generator(Protocol):
    def generate(
        self,
        request: ForgeRequest,
        *,
        json_schema: Mapping[str, object],
        metadata: GenerationMetadata,
    ) -> Mapping[str, object]: ...


class AnkiGateway(Protocol):
    def find_notes(self, query: str) -> list[int]: ...

    def add_notes(
        self,
        deck_name: str,
        units: Sequence[VocabUnit],
    ) -> list[int]: ...


class EventLogPort(Protocol):
    def log(
        self,
        event: str,
        unit_key: str,
        payload: dict[str, Any],
    ) -> Event: ...

    def read(
        self,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[Event]: ...


class ConfirmationPort(Protocol):
    def decide(self, preview: ForgePreview) -> ConfirmationDecision: ...
