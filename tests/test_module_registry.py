"""Tests for module registry."""

from __future__ import annotations

import pytest

from cock_monitor.platform.registry import get_registry, module_enabled, parse_enabled_modules


def test_parse_enabled_modules_inserts_core() -> None:
    assert parse_enabled_modules({"ENABLED_MODULES": "vless,mtproxy"}) == ["core", "vless", "mtproxy"]


def test_parse_enabled_modules_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        parse_enabled_modules({"ENABLED_MODULES": "nope"})


def test_registry_lists_all_modules() -> None:
    registry = get_registry()
    ids = {spec.id for spec in registry.all_specs()}
    assert ids == {"core", "vless", "mtproxy", "wg", "incident", "shaper"}


def test_module_enabled_from_env() -> None:
    env = {"ENABLED_MODULES": "core,wg,incident"}
    assert module_enabled("wg", env)
    assert not module_enabled("vless", env)
