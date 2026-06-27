"""Phase 9: HOP_LINKS only; incident does not hop-alert when hop module enabled."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cock_monitor.adapters import hop_links as hl
from cock_monitor.modules.incident import level, probes, sampler


def test_resolve_hop_links_raw_reads_hop_links_only() -> None:
    env = {
        "HOP_LINKS": "germany:dst:1.2.3.4:10089",
    }
    assert hl.resolve_hop_links_raw(env) == "germany:dst:1.2.3.4:10089"


def test_resolve_hop_links_raw_ignores_removed_incident_key() -> None:
    env = {"INCIDENT_HOP_LINKS": "germany:dst:1.2.3.4:10089"}
    assert hl.resolve_hop_links_raw(env) == ""


def test_incident_hop_level_enabled_without_hop_module() -> None:
    assert level.incident_hop_level_enabled({"ENABLED_MODULES": "core,incident"}) is True


def test_incident_hop_level_disabled_with_hop_module() -> None:
    assert level.incident_hop_level_enabled({"ENABLED_MODULES": "core,hop,incident"}) is False


def test_compute_level_ignores_hop_when_links_none() -> None:
    assert (
        level.compute_level(
            fill_pct=0,
            conn_warn=85,
            conn_crit=95,
            ping_max_loss=0,
            ping_loss_warn=20,
            dns_fail_streak=0,
            dns_streak_warn=3,
            tcp_enabled=0,
            tcp_fails=0,
            tcp_warn_fail=1,
            tcp_crit_fail=0,
            hop_links=None,
            hop_estab_warn=5,
            hop_estab_crit=20,
        )
        == "OK"
    )


def test_run_once_writes_hop_links_but_ok_level_with_hop_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENABLED_MODULES", "core,hop,incident")
    monkeypatch.setenv("INCIDENT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("INCIDENT_STATE_FILE", str(tmp_path / "state"))
    monkeypatch.setattr(sampler, "apply_incident_defaults", lambda: None)

    hop_data = {
        "enabled": 1,
        "links": [{"name": "germany", "estab": 99, "fin_wait": 0, "error": ""}],
    }

    monkeypatch.setattr(probes, "collect_hop_links", lambda: hop_data)
    monkeypatch.setattr(sampler, "collect_hop_links", lambda: hop_data)
    monkeypatch.setattr(probes, "collect_ping_legacy", lambda *_a, **_k: ([], 0))
    monkeypatch.setattr(probes, "collect_ping_groups", lambda: [])
    monkeypatch.setattr(probes, "collect_dns", lambda *_a, **_k: (1, 10, ""))
    monkeypatch.setattr(probes, "collect_conntrack", lambda: (0, 0, 0))
    monkeypatch.setattr(probes, "collect_tcp_states", lambda: {
        "estab": 0, "syn_recv": 0, "time_wait": 0, "fin_wait": 0, "close_wait": 0, "orphan": 0,
    })
    monkeypatch.setattr(probes, "collect_tcp_probes", lambda: {
        "enabled": 0,
        "totals": {
            "all": {"total": 0, "fails": 0},
            "local": {"total": 0, "fails": 0},
            "external": {"total": 0, "fails": 0},
        },
        "targets": {"local": "", "external": ""},
    })
    monkeypatch.setattr(
        "cock_monitor.adapters.linux_host.read_load_mem_from_proc",
        lambda: (0.1, 100000),
    )
    monkeypatch.setattr(probes, "collect_units", lambda: {})
    monkeypatch.setattr("cock_monitor.modules.incident.postmortem.state_load", lambda _p: {})
    monkeypatch.setattr("cock_monitor.modules.incident.postmortem.state_save", lambda *_a, **_k: None)
    monkeypatch.setattr("cock_monitor.modules.incident.postmortem.maybe_alert", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "cock_monitor.modules.incident.postmortem.incident_track_and_postmortem",
        lambda *_a, **_k: None,
    )

    assert sampler.run_once() == 0
    row = json.loads(list(tmp_path.glob("incident-*.jsonl"))[0].read_text(encoding="utf-8").strip())
    assert row["hop_links"]["enabled"] == 1
    assert row["level"] == "OK"
