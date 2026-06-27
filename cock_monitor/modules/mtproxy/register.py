"""MTProxy module registration."""

from __future__ import annotations

import sqlite3

from cock_monitor.modules.mtproxy.service import run_mtproxy_tick
from cock_monitor.modules.mtproxy.telegram_handlers import (
    handle_mt_status,
    handle_mt_threshold,
    handle_mt_today,
)
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def _migrate_mtproxy(conn: sqlite3.Connection) -> None:
    from cock_monitor.modules.mtproxy.repository import init_schema

    init_schema(conn)


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="mtproxy",
            label="MTProto proxy monitoring",
            depends_on=("core",),
            systemd_service="cock-monitor-mtproxy.service",
            systemd_timer="cock-monitor-mtproxy.timer",
            env_fragment="mtproxy.env",
            required_tools=("ss", "iptables", "pgrep"),
            apt_packages=("python3-matplotlib",),
            schema_migrate=_migrate_mtproxy,
            telegram_commands=(
                TelegramCommand("mt_status", "MTProxy live status", "mtproxy", handler=handle_mt_status),
                TelegramCommand("mt_today", "MTProxy 24h report + chart", "mtproxy", handler=handle_mt_today),
                TelegramCommand("mt_threshold", "Update MTProxy thresholds", "mtproxy", handler=handle_mt_threshold),
            ),
            run_tick=lambda env, dry: run_mtproxy_tick(env, dry_run=dry),
            daily_timer=True,
            daily_service_unit="cock-mtproxy-daily.service",
            daily_timer_unit="cock-mtproxy-daily.timer",
        )
    )
