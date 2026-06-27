"""Shaper module registration."""

from __future__ import annotations

from cock_monitor.modules.shaper.telegram_handlers import handle_cake_bw, run_shaper_tick
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, TelegramCommand


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="shaper",
            label="CPU-aware WAN egress shaper (CAKE)",
            depends_on=("core",),
            systemd_service="cock-monitor-shaper.service",
            systemd_timer="cock-monitor-shaper.timer",
            env_fragment="shaper.env",
            required_tools=("tc", "ip"),
            telegram_commands=(
                TelegramCommand("cake_bw", "Set CAKE bandwidth limit (Mbit)", "shaper", handler=handle_cake_bw),
            ),
            run_tick=lambda env, dry: run_shaper_tick(env, dry_run=dry),
        )
    )
