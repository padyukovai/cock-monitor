"""Incident severity level from probe readings."""

from __future__ import annotations

import os
from typing import Any

from cock_monitor.platform.registry import module_enabled


def incident_hop_level_enabled(env: dict[str, str] | None = None) -> bool:
    """False when hop module owns hop Telegram alerts (incident still samples JSONL)."""
    raw = dict(os.environ) if env is None else env
    return not module_enabled("hop", raw)


def compute_level(
    *,
    fill_pct: int,
    conn_warn: int,
    conn_crit: int,
    ping_max_loss: int,
    ping_loss_warn: int,
    dns_fail_streak: int,
    dns_streak_warn: int,
    tcp_enabled: int,
    tcp_fails: int,
    tcp_warn_fail: int,
    tcp_crit_fail: int,
    tcp_fin_wait: int = 0,
    tcp_fin_wait_warn: int = 0,
    tcp_close_wait: int = 0,
    tcp_close_wait_warn: int = 0,
    tcp_orphan: int = 0,
    tcp_orphan_warn: int = 0,
    hop_links: list[dict[str, Any]] | None = None,
    hop_estab_warn: int = 0,
    hop_estab_crit: int = 0,
    hop_fin_wait_warn: int = 0,
    hop_fin_wait_crit: int = 0,
) -> str:
    level = "OK"
    if fill_pct >= conn_crit:
        level = "CRIT"
    elif tcp_enabled == 1 and tcp_crit_fail > 0 and tcp_fails >= tcp_crit_fail:
        level = "CRIT"
    elif hop_links:
        for link in hop_links:
            estab = int(link.get("estab", 0) or 0)
            fin_wait = int(link.get("fin_wait", 0) or 0)
            if (hop_estab_crit > 0 and estab >= hop_estab_crit) or (
                hop_fin_wait_crit > 0 and fin_wait >= hop_fin_wait_crit
            ):
                level = "CRIT"
                break
    if level != "CRIT":
        if (
            fill_pct >= conn_warn
            or ping_max_loss >= ping_loss_warn
            or dns_fail_streak >= dns_streak_warn
            or (tcp_enabled == 1 and tcp_fails >= tcp_warn_fail)
            or (tcp_fin_wait_warn > 0 and tcp_fin_wait >= tcp_fin_wait_warn)
            or (tcp_close_wait_warn > 0 and tcp_close_wait >= tcp_close_wait_warn)
            or (tcp_orphan_warn > 0 and tcp_orphan >= tcp_orphan_warn)
        ):
            level = "WARN"
        elif hop_links:
            for link in hop_links:
                if str(link.get("error") or "").strip():
                    level = "WARN"
                    break
                estab = int(link.get("estab", 0) or 0)
                fin_wait = int(link.get("fin_wait", 0) or 0)
                if (hop_estab_warn > 0 and estab >= hop_estab_warn) or (
                    hop_fin_wait_warn > 0 and fin_wait >= hop_fin_wait_warn
                ):
                    level = "WARN"
                    break
    return level
