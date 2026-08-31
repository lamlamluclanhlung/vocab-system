"""Construct the deployment ArtifactStore only for a verified artifact root.

ArtifactStore.__init__ calls mkdir(parents=True, exist_ok=True), so building one
unconditionally would let an operation silently recreate a missing artifacts
directory. Bootstrap is the only path allowed to create it.
"""

from __future__ import annotations

from ..artifact_store import ArtifactStore
from .errors import RuntimePreflightError
from .layout import DeploymentLayout
from .normalize import ARTIFACT_SEAM, normalized


def open_deployment_artifact_store(layout: DeploymentLayout) -> ArtifactStore:
    """Return the deployment store, refusing when artifacts/ is absent."""
    if not layout.artifact_root.is_dir():
        raise RuntimePreflightError(
            "artifact root is absent; only bootstrap may create it"
        )
    with normalized(
        RuntimePreflightError, "artifact root", catching=ARTIFACT_SEAM
    ):
        return ArtifactStore(layout.artifact_root)
