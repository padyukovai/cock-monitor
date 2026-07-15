"""Core monitoring tick: conntrack + host metrics + alerts."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.modules.core.leak_alert import run_leak_alert
from cock_monitor.modules.core.leak_watchdog import run_leak_watchdog
from cock_monitor.modules.core.mem_alert import run_mem_alert
from cock_monitor.services.conntrack_check import run_conntrack_check


def run_core_tick(
    env_file: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Run conntrack check then MEM/leak alerts and leak watchdog.

    Alert Telegram failures are soft: they must not skip leak_watchdog, otherwise
    a down proxy leaves xray growing without auto-restart.
    """
    worst = 0
    rc = run_conntrack_check(env_file, dry_run_override=dry_run)
    if rc != 0:
        return rc
    for step in (run_mem_alert, run_leak_alert):
        rc = step(env_file, dry_run=dry_run)
        if rc != 0:
            worst = rc
    wd = run_leak_watchdog(env_file, dry_run=dry_run)
    if wd != 0:
        return wd
    return worst
