"""Shared runtime env helpers (module-agnostic)."""

from __future__ import annotations

import os
from pathlib import Path

from cock_monitor.config_loader import load_config


def get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def get_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def load_env_overwrite(path: Path) -> None:
    """Like bash `set -a; source file` — keys from file override process env."""
    loaded = load_config(path)
    for key, value in loaded.app.raw.items():
        os.environ[key] = value
