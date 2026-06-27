"""Tests for role presets and profile validation (phase 12)."""

from __future__ import annotations

from cock_monitor.config_check_cli import run as config_check_run
from cock_monitor.config_loader import validate_config
from cock_monitor.config_schema import AppConfig
from cock_monitor.platform.config import build_env_from_profile
from cock_monitor.platform.profile_validation import validate_profile_env
from cock_monitor.platform.roles import profile_for_role, resolve_install_profile


def test_profile_for_role_mtproxy_only() -> None:
    assert profile_for_role("mtproxy-only") == "stack-mtproxy"


def test_role_and_profile_equivalent_env() -> None:
    role_env = build_env_from_profile(profile_for_role("mtproxy-only"))
    profile_env = build_env_from_profile("stack-mtproxy")
    assert role_env == profile_env


def test_resolve_install_profile_role_wins() -> None:
    assert resolve_install_profile(role="mtproxy-only", profile="stack-3xui") == "stack-mtproxy"


def test_stack_mtproxy_lean_alerts() -> None:
    env = build_env_from_profile("stack-mtproxy")
    assert env["LA_ALERT_ENABLE"] == "0"
    assert env["MEM_ALERT_ENABLE"] == "0"


def test_validate_profile_env_rf3_missing_hop_links() -> None:
    env = build_env_from_profile("stack-rf3")
    env["HOP_LINKS"] = ""
    errors, warnings = validate_profile_env(env, profile="stack-rf3")
    assert any("HOP_LINKS" in msg for msg in errors)


def test_validate_profile_env_mtproxy_no_extra_modules() -> None:
    env = build_env_from_profile("stack-mtproxy")
    errors, warnings = validate_profile_env(env, profile="stack-mtproxy")
    assert not errors
    assert not warnings


def test_config_check_profile_mtproxy_ok(capsys=None) -> None:
    env = build_env_from_profile("stack-mtproxy")
    app = AppConfig.from_env_map(env)
    base = validate_config(app)
    profile_errors, profile_warnings = validate_profile_env(env, profile="stack-mtproxy")
    assert not profile_errors
    assert not profile_warnings
    assert base.ok or all("TELEGRAM" in e for e in base.errors)


def test_config_check_cli_profile_mtproxy() -> None:
    rc = config_check_run(["--profile", "stack-mtproxy"])
    assert rc == 0
