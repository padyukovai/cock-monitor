"""Core monitoring tick: conntrack + host metrics + alerts."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.modules.core.mem_alert import run_mem_alert
from cock_monitor.services.conntrack_check import run_conntrack_check


def run_core_tick(
    env_file: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Run conntrack check then optional MEM alert (same timer tick)."""
    rc = run_conntrack_check(env_file, dry_run_override=dry_run)
    if rc != 0:
        return rc
    return run_mem_alert(env_file, dry_run=dry_run)
