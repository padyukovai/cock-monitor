"""Tests for entry health module."""
from __future__ import annotations

from pathlib import Path

from cock_monitor.adapters.xray_error_log import XrayErrorLogTracker
from cock_monitor.domain.entry_health import EntryAlertThresholds, evaluate_entry_alerts
from cock_monitor.modules.hop.alerts import HopAlertThresholds, evaluate_hop_alerts


def _thresholds(**overrides) -> EntryAlertThresholds:
    base = EntryAlertThresholds(
        accept_primary_min_per_min=15.0,
        accept_secondary_min_per_min=30.0,
        accept_ratio_warn=0.25,
        accept_ratio_crit=0.10,
        tls_handshake_warn=5,
        tls_handshake_crit=20,
        io_timeout_warn=10,
        io_timeout_crit=30,
        require_hop_ok=True,
    )
    return EntryAlertThresholds(**{**base.__dict__, **overrides})


def test_accept_asymmetry_warn() -> None:
    alerts = evaluate_entry_alerts(
        host="rf3",
        interval_sec=60,
        accepts_by_inbound={"in-443-tcp": 5, "in-8443-tcp": 120},
        primary_inbound="in-443-tcp",
        secondary_inbound="in-8443-tcp",
        tls_handshake_delta=0,
        io_timeout_delta=0,
        hop_ok=True,
        thresholds=_thresholds(),
    )
    assert any(a.alert_type == "accept_asymmetry" and a.level == "CRIT" for a in alerts)


def test_accept_asymmetry_skipped_when_hop_down() -> None:
    alerts = evaluate_entry_alerts(
        host="rf3",
        interval_sec=60,
        accepts_by_inbound={"in-443-tcp": 5, "in-8443-tcp": 120},
        primary_inbound="in-443-tcp",
        secondary_inbound="in-8443-tcp",
        tls_handshake_delta=0,
        io_timeout_delta=0,
        hop_ok=False,
        thresholds=_thresholds(),
    )
    assert not any(a.alert_type == "accept_asymmetry" for a in alerts)


def test_tls_handshake_crit() -> None:
    alerts = evaluate_entry_alerts(
        host="rf3",
        interval_sec=60,
        accepts_by_inbound={},
        primary_inbound="in-443-tcp",
        secondary_inbound="in-8443-tcp",
        tls_handshake_delta=25,
        io_timeout_delta=0,
        hop_ok=True,
        thresholds=_thresholds(),
    )
    assert any(a.alert_type == "tls_handshake_errors" and a.level == "CRIT" for a in alerts)


def test_hop_alerts_ignore_tls_only_errors() -> None:
    th = HopAlertThresholds(5, 20, 20, 50, 3, 10, 80, 50)
    alerts = evaluate_hop_alerts(
        host="rf3",
        links=[],
        error_delta={
            "delta_total": 8,
            "delta_mux_fail": 0,
            "delta_conn_refused": 0,
            "delta_retry_exhausted": 0,
            "delta_tls_handshake": 8,
            "delta_io_timeout": 0,
        },
        probes=[],
        thresholds=th,
    )
    assert not any(a.alert_type == "xray_errors" for a in alerts)


def test_xray_error_log_tls_patterns(tmp_path: Path) -> None:
    log_path = tmp_path / "error.log"
    state_path = tmp_path / "state"
    log_path.write_text("", encoding="utf-8")
    tracker = XrayErrorLogTracker()
    tracker.restore_state(state_path, log_path)
    log_path.write_text(
        "2026/06/30 TLS handshake error from 79.139.177.188\n"
        "2026/06/30 i/o timeout\n",
        encoding="utf-8",
    )
    delta = tracker.poll()
    assert delta.delta_tls_handshake == 1
    assert delta.delta_io_timeout == 1
    assert delta.delta_hop_total == 0
