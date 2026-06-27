"""Tests for unified module enablement via ENABLED_MODULES."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.platform.registry import module_enabled
from cock_monitor.services import incident_sampler as ismp


@pytest.mark.parametrize(
    ("module_id", "env", "expected"),
    [
        ("incident", {"ENABLED_MODULES": "core,incident"}, True),
        ("incident", {"ENABLED_MODULES": "core"}, False),
        ("shaper", {"ENABLED_MODULES": "core,vless,shaper"}, True),
        ("mtproxy", {"ENABLED_MODULES": "core,mtproxy"}, True),
    ],
)
def test_module_enabled_from_env(module_id: str, env: dict[str, str], expected: bool) -> None:
    assert module_enabled(module_id, env) is expected


def test_mtproxy_config_enabled_from_enabled_modules() -> None:
    cfg = MtproxyConfig.from_env_map({"ENABLED_MODULES": "core,mtproxy"})
    assert cfg.enabled is True


def test_incident_run_once_skips_when_module_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENABLED_MODULES", "core")
    monkeypatch.setattr(ismp, "apply_incident_defaults", lambda: None)
    called = {"collect": 0}

    def fake_collect(*_a, **_k):
        called["collect"] += 1
        return [], 0

    monkeypatch.setattr(ismp, "collect_ping_legacy", fake_collect)
    assert ismp.run_once() == 0
    assert called["collect"] == 0


def test_shaper_script_active_with_enabled_modules(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "bin" / "cock-cpu-shaper.sh"
    state = tmp_path / "cpu_shaper.state"
    status = tmp_path / "cpu_shaper.status"
    env_file = tmp_path / "shaper.env"
    env_file.write_text(
        "ENABLED_MODULES=core,shaper\n"
        "SHAPER_IFACE=lo\n"
        "SHAPER_MEASURE_SLEEP_SEC=0\n"
        f"SHAPER_STATE_FILE={state}\n"
        f"SHAPER_STATUS_FILE={status}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(script), "--dry-run", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    combined = proc.stdout + proc.stderr
    assert "not in ENABLED_MODULES" not in combined


def test_shaper_script_disabled_without_module(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "bin" / "cock-cpu-shaper.sh"
    env_file = tmp_path / "shaper.env"
    env_file.write_text(
        "ENABLED_MODULES=core\n"
        "SHAPER_IFACE=lo\n"
        f"SHAPER_STATE_FILE={tmp_path / 'cpu_shaper.state'}\n"
        f"SHAPER_STATUS_FILE={tmp_path / 'cpu_shaper.status'}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(script), "--dry-run", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "not in ENABLED_MODULES" in proc.stderr
