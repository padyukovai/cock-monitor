from __future__ import annotations

from cock_monitor.platform.registry import get_registry


def test_registry_commands_hides_mtproxy_and_shaper_when_disabled() -> None:
    registry = get_registry()
    commands = registry.telegram_commands({"ENABLED_MODULES": "core,vless"})
    names = [c.name for c in commands]
    assert "status" in names
    assert "chart" in names
    assert "vless_delta" in names
    assert "help" in names
    assert not any(name.startswith("mt_") for name in names)
    assert "cake_bw" not in names


def test_registry_commands_includes_optional_modules_when_enabled() -> None:
    registry = get_registry()
    commands = registry.telegram_commands({"ENABLED_MODULES": "core,vless,mtproxy,shaper"})
    names = [c.name for c in commands]
    assert "cake_bw" in names
    assert "mt_status" in names
    assert "mt_today" in names
    assert "mt_threshold" in names
