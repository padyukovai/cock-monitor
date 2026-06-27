"""Hop module registration."""

from __future__ import annotations

from cock_monitor.modules.hop.service import run_hop_collect
from cock_monitor.modules.hop.storage import migrate_hop_schema
from cock_monitor.modules.hop.telegram_handlers import handle_hop_status
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="hop",
            label="VLESS hop monitoring",
            depends_on=("core",),
            systemd_service="cock-monitor-hop.service",
            systemd_timer="cock-monitor-hop.timer",
            env_fragment="hop.env",
            required_tools=("ss", "curl"),
            schema_migrate=migrate_hop_schema,
            telegram_commands=(
                TelegramCommand("hop_status", "VLESS hop link snapshot", "hop", handler=handle_hop_status),
            ),
            run_tick=lambda env, dry: run_hop_collect(env, dry_run=dry),
        )
    )
