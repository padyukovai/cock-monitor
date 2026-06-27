"""Core module registration."""

from __future__ import annotations

from cock_monitor.modules.core.service import run_core_tick
from cock_monitor.modules.core.telegram_handlers import handle_chart, handle_status
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def register(registry: ModuleRegistry) -> None:
    from cock_monitor.modules.core.storage import migrate_core_schema

    registry.register(
        ModuleSpec(
            id="core",
            label="Core host monitoring (conntrack, CPU, RAM)",
            systemd_service="cock-monitor-core.service",
            systemd_timer="cock-monitor-core.timer",
            env_fragment="core.env",
            apt_packages=("python3-matplotlib",),
            required_tools=("conntrack",),
            schema_migrate=migrate_core_schema,
            telegram_commands=(
                TelegramCommand("status", "Full host + conntrack status", "core", handler=handle_status),
                TelegramCommand("chart", "PNG chart (conntrack + host, 24h)", "core", handler=handle_chart),
                TelegramCommand("help", "Show enabled module commands", "core"),
            ),
            run_tick=lambda env, dry: run_core_tick(env, dry_run=dry),
            daily_timer=True,
            daily_service_unit="cock-monitor-daily.service",
            daily_timer_unit="cock-monitor-daily.timer",
        )
    )
