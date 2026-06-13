"""Core /status report."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.services.status_report import build_status_report


def build_core_status(env_file: Path) -> str:
    return build_status_report(env_file)
