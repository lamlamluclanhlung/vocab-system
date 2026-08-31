"""One fresh Wave C R/W attempt, frozen by D71 section 14.

DisplayPermit is in-memory, single-use, and non-reconstructible, so a fresh
attempt is one operation in one process. The terminal action is collected only
after the exact stimulus has actually been displayed and its permit consumed:
D67 owns refusal and explicit skip as learner actions, so neither may be
preselected at invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, cast

from ..artifact_json import strict_json_loads
from ..artifact_store import ArtifactStore
from ..response_capture import (
    close_text_submission,
    record_explicit_skip,
    record_refusal,
)
from ..session import load_session_manifest
from .assessment_session import render_stimulus_bytes, stimulus_artifact_ref
from .errors import RuntimeAttemptError
from .layout import DeploymentLayout
from .normalize import (
    ARTIFACT_SEAM,
    ATTEMPT_SEAM,
    FILESYSTEM_SEAM,
    MANIFEST_SEAM,
    normalized,
)

SUBMIT = "SUBMIT"
SKIP = "SKIP"
REFUSE = "REFUSE"

TERMINAL_ACTIONS: frozenset[str] = frozenset({SUBMIT, SKIP, REFUSE})

WAVE_C_CHANNELS: frozenset[str] = frozenset({"R", "W"})


class AttemptPort(Protocol):
    """The narrow display and interaction boundary, injected for tests."""

    def display_stimulus(self, payload: bytes) -> None:
        """Present the exact verified stimulus bytes to the learner."""

    def ask_terminal_action(self) -> str:
        """Return exactly one of SUBMIT, SKIP, or REFUSE, after display."""


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    """The durable result of one fresh attempt."""

    attempt_id: str
    unit_key: str
    channel: str
    action: str
    receipt_kind: str


def _manifest_item(manifest: object, item_ordinal: int) -> dict[str, object]:
    decoded = strict_json_loads(manifest.canonical_bytes)  # type: ignore[attr-defined]
    items = decoded["items"]  # type: ignore[index]
    matching = [
        item
        for item in items
        if type(item) is dict and item.get("item_ordinal") == item_ordinal
    ]
    if len(matching) != 1:
        raise RuntimeAttemptError(
            f"session manifest has no unique item_ordinal {item_ordinal}"
        )
    return matching[0]


def verify_stimulus_artifact(
    item: dict[str, object],
    artifact_store: ArtifactStore,
) -> bytes:
    """Rederive the expected bytes and prove the stored artifact matches.

    The manifest stimulus is the source of truth for what should be shown, so a
    stored artifact that disagrees with it fails closed before any reservation.
    """
    channel = item["channel"]
    if type(channel) is not str or channel not in WAVE_C_CHANNELS:
        raise RuntimeAttemptError(
            f"Wave C v1 operates only R and W, not {channel!r}"
        )
    stimulus = item["stimulus"]
    if not isinstance(stimulus, dict):
        raise RuntimeAttemptError("manifest item stimulus is malformed")

    expected = render_stimulus_bytes(channel, stimulus)
    expected_ref = stimulus_artifact_ref(expected)
    recorded_ref = item["stimulus_artifact_ref"]
    if recorded_ref != expected_ref:
        raise RuntimeAttemptError(
            "stimulus_artifact_ref does not match the manifest stimulus"
        )
    with normalized(
        RuntimeAttemptError,
        "stimulus artifact could not be read",
        catching=ARTIFACT_SEAM,
    ):
        stored = artifact_store.read(recorded_ref)
    if stored != expected:
        raise RuntimeAttemptError(
            "stored stimulus artifact bytes differ from the manifest stimulus"
        )
    return expected


def run_fresh_attempt(
    layout: DeploymentLayout,
    *,
    session_id: str,
    item_ordinal: int,
    artifact_store: ArtifactStore,
    port: AttemptPort,
    response_file: object = None,
    now: Callable[[], str] | None = None,
) -> AttemptOutcome:
    """Reserve, display, consume, then collect exactly one terminal action."""
    if not isinstance(artifact_store, ArtifactStore):
        raise RuntimeAttemptError("artifact_store must be an ArtifactStore")
    if type(item_ordinal) is not int or item_ordinal < 0:
        raise RuntimeAttemptError("item_ordinal must be a non-negative integer")
    clock = now if now is not None else (
        lambda: datetime.now(timezone.utc).isoformat()
    )

    # Imported here so the exposure authority is exercised through its own
    # module rather than being re-exported by this composition layer.
    from ..exposure import reserve_exposure

    with normalized(
        RuntimeAttemptError,
        "session manifest could not be loaded",
        catching=MANIFEST_SEAM,
    ):
        manifest = load_session_manifest(layout.session_root, session_id)
    item = _manifest_item(manifest, item_ordinal)

    stimulus_bytes = verify_stimulus_artifact(item, artifact_store)

    with normalized(
        RuntimeAttemptError,
        "exposure could not be reserved",
        catching=ATTEMPT_SEAM,
    ):
        permit = reserve_exposure(
            exposure_path=layout.exposure_path,
            capture_path=layout.capture_path,
            disposition_path=layout.disposition_path,
            artifact_store=artifact_store,
            session_root=layout.session_root,
            session_id=session_id,
            item_ordinal=item_ordinal,
            reserved_at=clock(),
        )

    port.display_stimulus(stimulus_bytes)
    permit.consume()

    action = port.ask_terminal_action()
    if type(action) is not str or action not in TERMINAL_ACTIONS:
        raise RuntimeAttemptError(
            f"terminal action must be one of {sorted(TERMINAL_ACTIONS)}"
        )

    common = {
        "exposure_path": layout.exposure_path,
        "capture_path": layout.capture_path,
        "disposition_path": layout.disposition_path,
        "artifact_store": artifact_store,
        "display_permit": permit,
    }

    if action == SUBMIT:
        if response_file is None:
            raise RuntimeAttemptError(
                "SUBMIT requires a response file; absence is never reinterpreted "
                "as no_response"
            )
        with normalized(
            RuntimeAttemptError,
            "response file could not be read",
            catching=FILESYSTEM_SEAM,
        ):
            raw_bytes = response_file.read_bytes()
        moment = clock()
        with normalized(
            RuntimeAttemptError,
            "text submission could not be closed",
            catching=ATTEMPT_SEAM,
        ):
            receipt = close_text_submission(
                raw_bytes=raw_bytes,
                captured_at=moment,
                disposed_at=moment,
                **common,
            )
    elif action == SKIP:
        with normalized(
            RuntimeAttemptError,
            "explicit skip could not be recorded",
            catching=ATTEMPT_SEAM,
        ):
            receipt = record_explicit_skip(disposed_at=clock(), **common)
    else:
        with normalized(
            RuntimeAttemptError,
            "refusal could not be recorded",
            catching=ATTEMPT_SEAM,
        ):
            receipt = record_refusal(disposed_at=clock(), **common)

    return AttemptOutcome(
        attempt_id=permit.attempt_id,
        unit_key=cast(str, item["unit_key"]),
        channel=cast(str, item["channel"]),
        action=action,
        receipt_kind=type(receipt).__name__,
    )
