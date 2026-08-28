"""Closed runtime configuration schema frozen by D70 section 6."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..artifact_json import ArtifactJSONError, strict_json_loads
from .errors import RuntimeConfigError


CONFIG_VERSION = 1

CONFIG_KEYS: frozenset[str] = frozenset(
    {"config_version", "data_root", "corpus_root", "anki"}
)

ANKI_CONFIG_KEYS: frozenset[str] = frozenset(
    {"endpoint", "timeout", "deck_name"}
)

_DEPLOYMENT_PATH_KEYS: tuple[str, ...] = ("data_root", "corpus_root")


@dataclass(frozen=True, slots=True)
class AnkiConfig:
    """The closed AnkiConnect section of one runtime configuration."""

    endpoint: str
    timeout: float
    deck_name: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """One validated runtime configuration addressing one deployment."""

    config_version: int
    data_root: Path
    corpus_root: Path
    anki: AnkiConfig
    source_path: Path


def validated_deployment_path(value: object, name: str) -> Path:
    """Require an absolute path string carrying no '.' or '..' component."""
    if type(value) is not str or not value:
        raise RuntimeConfigError(f"{name} must be a non-empty string")
    # The grammar is checked on the literal text the human wrote, because
    # pathlib silently collapses a '.' component and would hide it.
    for segment in value.replace("\\", "/").split("/"):
        if segment in {".", ".."}:
            raise RuntimeConfigError(
                f"{name} must not contain a '.' or '..' component"
            )
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeConfigError(f"{name} must be an absolute path")
    return path


def _validated_anki(raw: object) -> AnkiConfig:
    if not isinstance(raw, dict):
        raise RuntimeConfigError("anki must be a JSON object")
    keys = set(raw)
    unknown = sorted(keys - ANKI_CONFIG_KEYS)
    if unknown:
        raise RuntimeConfigError(f"anki has unknown keys: {unknown}")
    missing = sorted(ANKI_CONFIG_KEYS - keys)
    if missing:
        raise RuntimeConfigError(f"anki is missing keys: {missing}")

    endpoint = raw["endpoint"]
    if type(endpoint) is not str or not endpoint:
        raise RuntimeConfigError("anki.endpoint must be a non-empty string")

    timeout = raw["timeout"]
    if type(timeout) is bool or type(timeout) not in (int, float):
        raise RuntimeConfigError("anki.timeout must be a number")
    if not math.isfinite(float(timeout)) or float(timeout) <= 0:
        raise RuntimeConfigError("anki.timeout must be finite and positive")

    deck_name = raw["deck_name"]
    if type(deck_name) is not str or not deck_name:
        raise RuntimeConfigError("anki.deck_name must be a non-empty string")
    if deck_name != deck_name.strip():
        raise RuntimeConfigError(
            "anki.deck_name must not have leading or trailing whitespace"
        )

    return AnkiConfig(
        endpoint=endpoint,
        timeout=float(timeout),
        deck_name=deck_name,
    )


def validated_config_mapping(raw: object, source_path: Path) -> RuntimeConfig:
    """Validate one decoded configuration object against the closed schema."""
    if not isinstance(raw, dict):
        raise RuntimeConfigError("configuration must be a JSON object")
    keys = set(raw)
    unknown = sorted(keys - CONFIG_KEYS)
    if unknown:
        raise RuntimeConfigError(f"configuration has unknown keys: {unknown}")
    missing = sorted(CONFIG_KEYS - keys)
    if missing:
        raise RuntimeConfigError(f"configuration is missing keys: {missing}")

    config_version = raw["config_version"]
    if type(config_version) is not int or config_version != CONFIG_VERSION:
        raise RuntimeConfigError(
            f"config_version must be exactly {CONFIG_VERSION}"
        )

    paths = {
        name: validated_deployment_path(raw[name], name)
        for name in _DEPLOYMENT_PATH_KEYS
    }

    return RuntimeConfig(
        config_version=config_version,
        data_root=paths["data_root"],
        corpus_root=paths["corpus_root"],
        anki=_validated_anki(raw["anki"]),
        source_path=source_path,
    )


def load_config(path: object) -> RuntimeConfig:
    """Load and validate one explicit configuration file, failing closed."""
    if not isinstance(path, Path):
        raise RuntimeConfigError("configuration path must be a pathlib.Path")
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise RuntimeConfigError(
            f"configuration file could not be read: {exc}"
        ) from exc
    try:
        decoded = strict_json_loads(raw_bytes)
    except (ArtifactJSONError, TypeError) as exc:
        raise RuntimeConfigError(
            f"configuration is not strict JSON: {exc}"
        ) from exc
    return validated_config_mapping(decoded, path)
