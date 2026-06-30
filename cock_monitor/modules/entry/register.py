"""Entry module registration."""

from __future__ import annotations

from cock_monitor.modules.entry.service import run_entry_collect
from cock_monitor.modules.entry.storage import migrate_entry_schema
from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="entry",
            label="Entry node health (accepts + TLS errors)",
            depends_on=("core",),
            systemd_service="cock-monitor-entry.service",
            systemd_timer="cock-monitor-entry.timer",
            env_fragment="entry.env",
            required_tools=(),
            schema_migrate=migrate_entry_schema,
            run_tick=lambda env, dry: run_entry_collect(env, dry_run=dry),
        )
    )
