"""Tests for unified module enablement (phase 7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.platform.legacy_enable import resolve_module_enabled
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
def test_resolve_module_enabled_via_enabled_modules(
    module_id: str, env: dict[str, str], expected: bool
) -> None:
    assert resolve_module_enabled(module_id, env) is expected


def test_resolve_module_enabled_legacy_incident_warns() -> None:
    env = {"INCIDENT_SAMPLER_ENABLE": "1", "ENABLED_MODULES": "core"}
    with pytest.warns(DeprecationWarning, match="INCIDENT_SAMPLER_ENABLE"):
        assert resolve_module_enabled("incident", env) is True


def test_resolve_module_enabled_legacy_shaper_flag() -> None:
    env = {"SHAPER_ENABLE": "1", "ENABLED_MODULES": "core"}
    with pytest.warns(DeprecationWarning, match="SHAPER_ENABLE"):
        assert resolve_module_enabled("shaper", env) is True


def test_mtproxy_config_enabled_without_legacy_flag() -> None:
    cfg = MtproxyConfig.from_env_map({"ENABLED_MODULES": "core,mtproxy"})
    assert cfg.enabled is True


def test_incident_run_once_skips_when_module_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENABLED_MODULES", "core")
    monkeypatch.setenv("INCIDENT_SAMPLER_ENABLE", "0")
    monkeypatch.setattr(ismp, "apply_incident_defaults", lambda: None)
    called = {"collect": 0}

    def fake_collect(*_a, **_k):
        called["collect"] += 1
        return [], 0

    monkeypatch.setattr(ismp, "collect_ping_legacy", fake_collect)
    assert ismp.run_once() == 0
    assert called["collect"] == 0


def test_incident_run_once_runs_with_enabled_modules(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENABLED_MODULES", "core,incident")
    monkeypatch.setenv("INCIDENT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("INCIDENT_STATE_FILE", str(tmp_path / "state"))
    monkeypatch.setattr(ismp, "apply_incident_defaults", lambda: None)

    def fake_collect(*_a, **_k):
        return [], 0

    monkeypatch.setattr(ismp, "collect_ping_legacy", fake_collect)
    monkeypatch.setattr(ismp, "collect_ping_groups", lambda: [])
    monkeypatch.setattr(ismp, "collect_dns", lambda *_a, **_k: (1, 10, ""))
    monkeypatch.setattr(ismp, "collect_conntrack", lambda: (0, 0, 0))
    monkeypatch.setattr(ismp, "collect_tcp_states", lambda: {
        "estab": 0, "syn_recv": 0, "time_wait": 0, "fin_wait": 0, "close_wait": 0, "orphan": 0,
    })
    monkeypatch.setattr(ismp, "collect_hop_links", lambda: {"enabled": 0, "links": []})
    monkeypatch.setattr(ismp, "collect_tcp_probes", lambda: {
        "enabled": 0,
        "totals": {
            "all": {"total": 0, "fails": 0},
            "local": {"total": 0, "fails": 0},
            "external": {"total": 0, "fails": 0},
        },
        "targets": {"local": "", "external": ""},
    })
    monkeypatch.setattr(ismp, "read_load_mem_from_proc", lambda: (0.1, 100000))
    monkeypatch.setattr(ismp, "collect_units", lambda: [])
    monkeypatch.setattr(ismp, "compute_level", lambda **_k: "OK")
    monkeypatch.setattr(ismp, "state_load", lambda _p: {})
    monkeypatch.setattr(ismp, "state_save", lambda *_a, **_k: None)
    monkeypatch.setattr(ismp, "maybe_alert", lambda *_a, **_k: None)
    monkeypatch.setattr(ismp, "incident_track_and_postmortem", lambda *_a, **_k: None)

    assert ismp.run_once() == 0
    assert list(tmp_path.glob("incident-*.jsonl"))


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
    assert "SHAPER_ENABLE=0" not in combined
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
