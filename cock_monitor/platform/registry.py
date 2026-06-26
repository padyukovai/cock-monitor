"""Module registry — single source of truth for enabled modules and their capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

MODULE_IDS = frozenset({"core", "vless", "mtproxy", "wg", "incident", "shaper", "hop"})


@dataclass(frozen=True)
class TelegramCommand:
    name: str
    help_text: str
    module_id: str


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    label: str
    depends_on: tuple[str, ...] = ()
    systemd_service: str = ""
    systemd_timer: str = ""
    env_fragment: str = ""
    apt_packages: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    schema_migrate: Callable[..., None] | None = None
    telegram_commands: tuple[TelegramCommand, ...] = ()
    daily_timer: bool = False

    def service_unit(self) -> str:
        return self.systemd_service or f"cock-monitor-{self.id}.service"

    def timer_unit(self) -> str:
        return self.systemd_timer or f"cock-monitor-{self.id}.timer"


def parse_enabled_modules(env: dict[str, str]) -> list[str]:
    raw = env.get("ENABLED_MODULES", "core").strip()
    if not raw:
        return ["core"]
    ids: list[str] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if part not in MODULE_IDS:
            raise ValueError(f"unknown module in ENABLED_MODULES: {part}")
        if part not in ids:
            ids.append(part)
    if "core" not in ids:
        ids.insert(0, "core")
    return ids


def module_enabled(module_id: str, env: dict[str, str]) -> bool:
    return module_id in parse_enabled_modules(env)


class ModuleRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ModuleSpec] = {}

    def register(self, spec: ModuleSpec) -> None:
        if spec.id in self._specs:
            raise ValueError(f"duplicate module id: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, module_id: str) -> ModuleSpec:
        return self._specs[module_id]

    def all_specs(self) -> list[ModuleSpec]:
        return [self._specs[k] for k in sorted(self._specs)]

    def enabled_specs(self, env: dict[str, str]) -> list[ModuleSpec]:
        enabled = set(parse_enabled_modules(env))
        out: list[ModuleSpec] = []
        for mid in parse_enabled_modules(env):
            if mid in self._specs:
                out.append(self._specs[mid])
        for spec in out:
            for dep in spec.depends_on:
                if dep not in enabled:
                    raise ValueError(f"module {spec.id} requires {dep} in ENABLED_MODULES")
        return out

    def telegram_commands(self, env: dict[str, str]) -> list[TelegramCommand]:
        cmds: list[TelegramCommand] = []
        seen: set[str] = set()
        for spec in self.enabled_specs(env):
            for cmd in spec.telegram_commands:
                if cmd.name not in seen:
                    cmds.append(cmd)
                    seen.add(cmd.name)
        return cmds

    def systemd_timers(self, env: dict[str, str], *, include_telegram: bool = True) -> list[str]:
        timers: list[str] = []
        for spec in self.enabled_specs(env):
            if spec.id == "core" or spec.systemd_timer or spec.systemd_service:
                timers.append(spec.timer_unit())
        if include_telegram:
            timers.append("cock-monitor-telegram.timer")
        return sorted(set(timers))

    def systemd_services(self, env: dict[str, str], *, include_telegram: bool = True) -> list[str]:
        services: list[str] = []
        for spec in self.enabled_specs(env):
            services.append(spec.service_unit())
        if include_telegram:
            services.append("cock-monitor-telegram.service")
        return sorted(set(services))

    def apt_packages(self, env: dict[str, str]) -> set[str]:
        base = {"python3", "python3-venv", "python3-pip", "curl", "sqlite3", "conntrack"}
        for spec in self.enabled_specs(env):
            base.update(spec.apt_packages)
        return base


_registry: ModuleRegistry | None = None


def _register_all(registry: ModuleRegistry) -> None:
    from cock_monitor.modules.core import register as register_core
    from cock_monitor.modules.hop import register as register_hop
    from cock_monitor.modules.incident import register as register_incident
    from cock_monitor.modules.mtproxy import register as register_mtproxy
    from cock_monitor.modules.shaper import register as register_shaper
    from cock_monitor.modules.vless import register as register_vless
    from cock_monitor.modules.wg import register as register_wg

    register_core(registry)
    register_vless(registry)
    register_mtproxy(registry)
    register_wg(registry)
    register_incident(registry)
    register_shaper(registry)
    register_hop(registry)


def get_registry() -> ModuleRegistry:
    global _registry
    if _registry is None:
        _registry = ModuleRegistry()
        _register_all(_registry)
    return _registry
