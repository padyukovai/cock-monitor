"""Incident module entry for run_cli."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.modules.incident.env import load_env_overwrite
from cock_monitor.modules.incident.sampler import run_once


def run_incident_tick(env_file: Path) -> int:
    load_env_overwrite(env_file)
    return run_once()
