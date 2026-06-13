"""Incident sampler registration."""

from __future__ import annotations

from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec


def register(registry: ModuleRegistry) -> None:
    registry.register(
        ModuleSpec(
            id="incident",
            label="Incident sampler (JSONL snapshots)",
            depends_on=("core",),
            systemd_service="cock-monitor-incident.service",
            systemd_timer="cock-monitor-incident.timer",
            env_fragment="incident.env",
            required_tools=("ping", "timeout", "getent", "ss", "systemctl", "ip"),
        )
    )
