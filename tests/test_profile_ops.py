"""Tests for profile ops metadata (phase 11)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cock_monitor.install_cli import run_post_install_scripts
from cock_monitor.platform.config import PROFILE_OPS_KEYS, build_env_from_profile
from cock_monitor.platform.profile_ops import format_post_install_checklist, load_profile_ops


def test_load_profile_ops_stack_rf3() -> None:
    ops = load_profile_ops("stack-rf3")
    assert ops.post_install_scripts == ("install/rf3/setup-hop-probe.sh",)
    assert ops.preflight_systemd_units == ("xray-hop-probe.service",)
    assert ops.preflight_tcp_ports == (10891, 10892)


def test_build_env_excludes_profile_ops_keys() -> None:
    env = build_env_from_profile("stack-rf3")
    assert "POST_INSTALL_SCRIPTS" not in env
    assert "PREFLIGHT_SYSTEMD_UNITS" not in env
    assert "PREFLIGHT_TCP_PORTS" not in env
    assert env["HOP_LINKS"]


def test_format_post_install_checklist_rf3() -> None:
    lines = format_post_install_checklist("stack-rf3")
    assert any("setup-hop-probe.sh" in line for line in lines)
    assert any("manual" in line.lower() for line in lines)


def test_format_post_install_checklist_core_empty() -> None:
    assert format_post_install_checklist("core") == []


def test_run_post_install_missing_script_returns_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rc = run_post_install_scripts("stack-rf3", Path(tmp))
    assert rc == 1


def test_profile_ops_keys_frozen() -> None:
    assert "POST_INSTALL_SCRIPTS" in PROFILE_OPS_KEYS
