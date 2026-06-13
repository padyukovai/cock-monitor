"""VLESS module registration."""

from __future__ import annotations

import sqlite3

from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def _migrate_vless(conn: sqlite3.Connection) -> None:
    from cock_monitor.storage.vless_repository import ensure_report_tables

    ensure_report_tables(conn)
    conn.commit()


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="vless",
            label="3x-ui VLESS traffic reports",
            depends_on=("core",),
            systemd_service="cock-monitor-vless.service",
            systemd_timer="cock-monitor-vless.timer",
            env_fragment="vless.env",
            apt_packages=("python3-matplotlib",),
            schema_migrate=_migrate_vless,
            daily_timer=True,
            telegram_commands=(
                TelegramCommand("vless_delta", "VLESS usage delta report", "vless"),
            ),
        )
    )
