"""WG module registration."""

from __future__ import annotations

from cock_monitor.modules.wg.service import run_wg_collect
from cock_monitor.modules.wg.storage import migrate_wg_schema
from cock_monitor.modules.wg.telegram_handlers import handle_wg_status
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="wg",
            label="WireGuard peer monitoring",
            depends_on=("core",),
            systemd_service="cock-monitor-wg.service",
            systemd_timer="cock-monitor-wg.timer",
            env_fragment="wg.env",
            required_tools=("wg",),
            schema_migrate=migrate_wg_schema,
            telegram_commands=(
                TelegramCommand("wg_status", "WireGuard peers snapshot", "wg", handler=handle_wg_status),
            ),
            run_tick=lambda env, dry: run_wg_collect(env, dry_run=dry),
        )
    )
