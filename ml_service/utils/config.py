"""Configuration loader for the ML service.

Loads ``config.yaml`` (or the ``.example`` fallback) from the ml_service
directory and exposes it as a plain dictionary plus a few typed helpers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

ML_SERVICE_DIR = Path(__file__).resolve().parent.parent


def _resolve_config_path(config_path: str | os.PathLike | None = None) -> Path:
    if config_path is not None:
        return Path(config_path)
    primary = ML_SERVICE_DIR / "config.yaml"
    if primary.exists():
        return primary
    example = ML_SERVICE_DIR / "config.yaml.example"
    if example.exists():
        return example
    raise FileNotFoundError(
        "No config.yaml or config.yaml.example found in ml_service/."
    )


def load_config(config_path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load and return the YAML configuration as a dictionary."""
    path = _resolve_config_path(config_path)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_config_path"] = str(path)
    cfg["_ml_service_dir"] = str(ML_SERVICE_DIR)
    return cfg


class AppConfig:
    """Thin attribute-style wrapper over the config dictionary."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def mqtt(self) -> Dict[str, Any]:
        return self._data["mqtt"]

    @property
    def topics(self) -> Dict[str, Any]:
        return self._data["topics"]

    @property
    def features(self) -> list[str]:
        return self._data["model"]["features"]

    @property
    def classes(self) -> list[str]:
        return self._data["classes"]

    def resolve_path(self, relative: str) -> Path:
        """Resolve a config-declared path relative to the ml_service dir."""
        return (ML_SERVICE_DIR / relative).resolve()