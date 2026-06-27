"""Shared platform: registry, config, storage, telegram."""

from cock_monitor.platform.registry import ModuleRegistry, ModuleSpec, get_registry

__all__ = ["ModuleRegistry", "ModuleSpec", "get_registry"]
