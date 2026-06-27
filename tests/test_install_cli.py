"""Tests for install_cli unit selection (phase 8)."""

from __future__ import annotations

from cock_monitor.install_cli import collect_install_units
from cock_monitor.platform.config import build_env_from_profile
from cock_monitor.platform.daily_runners import exec_start_argv


def test_stack_3xui_installs_core_and_vless_daily_timers() -> None:
    env = build_env_from_profile("stack-3xui")
    units = collect_install_units(env)
    assert "cock-monitor-daily.timer" in units
    assert "cock-monitor-daily.service" in units
    assert "cock-vless-daily.timer" in units
    assert "cock-vless-daily.service" in units
    assert "cock-monitor-core.timer" in units
    assert "cock-mtproxy-daily.timer" not in units


def test_stack_mtproxy_installs_mtproxy_daily_timer() -> None:
    env = build_env_from_profile("stack-mtproxy")
    units = collect_install_units(env)
    assert "cock-mtproxy-daily.timer" in units
    assert "cock-mtproxy-daily.service" in units
    assert "cock-monitor-daily.timer" in units
    assert "cock-vless-daily.timer" not in units


def test_stack_rf3_no_daily_except_core() -> None:
    env = build_env_from_profile("stack-rf3")
    units = collect_install_units(env)
    assert "cock-monitor-daily.timer" in units
    assert "cock-vless-daily.timer" not in units
    assert "cock-mtproxy-daily.timer" not in units
    assert "cock-monitor-hop.timer" in units


def test_stack_exit_node_alias_matches_3xui_modules() -> None:
    exit_env = build_env_from_profile("stack-exit-node")
    xui_env = build_env_from_profile("stack-3xui")
    assert exit_env["ENABLED_MODULES"] == xui_env["ENABLED_MODULES"]
    assert collect_install_units(exit_env) == collect_install_units(xui_env)


def test_daily_runner_exec_start_argv() -> None:
    argv = exec_start_argv(
        __import__("pathlib").Path("/opt/cock-monitor/.venv/bin/python"),
        __import__("pathlib").Path("/etc/cock-monitor.env"),
        "cock-monitor-daily.service",
    )
    assert argv is not None
    assert argv[:4] == [
        "/opt/cock-monitor/.venv/bin/python",
        "-m",
        "cock_monitor",
        "daily-chart",
    ]
    assert "/etc/cock-monitor.env" in argv
