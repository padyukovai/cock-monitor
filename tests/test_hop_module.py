"""Tests for hop module."""
from __future__ import annotations

from cock_monitor.adapters import hop_links as hl
from cock_monitor.adapters.xray_error_log import XrayErrorLogTracker
from cock_monitor.modules.hop.alerts import HopAlertThresholds, evaluate_hop_alerts
from cock_monitor.modules.hop.probe import parse_hop_probe_spec, parse_hop_probes_env


def test_parse_hop_probe_spec() -> None:
    spec = parse_hop_probe_spec(
        "germany:socks5h://127.0.0.1:10891:https://api.ipify.org?format=json:144.31.154.44"
    )
    assert spec is not None
    assert spec.name == "germany"
    assert spec.proxy == "socks5h://127.0.0.1:10891"
    assert spec.url == "https://api.ipify.org?format=json"
    assert spec.expect_substr == "144.31.154.44"


def test_parse_hop_probes_env() -> None:
    raw = (
        "germany:socks5h://127.0.0.1:10891:https://api.ipify.org?format=json:144.31.154.44,"
        "usa:socks5h://127.0.0.1:10892:https://api.ipify.org?format=json:153.75.246.28"
    )
    specs = parse_hop_probes_env(raw)
    assert len(specs) == 2
    assert specs[1].name == "usa"


def test_evaluate_hop_alerts_estab_crit() -> None:
    th = HopAlertThresholds(5, 20, 20, 50, 3, 10, 80, 50)
    alerts = evaluate_hop_alerts(
        host="rf3",
        links=[{"name": "usa", "estab": 30, "fin_wait": 0, "error": ""}],
        error_delta={"delta_total": 0},
        probes=[],
        thresholds=th,
    )
    assert any(a.level == "CRIT" and a.alert_type == "hop_estab_high" for a in alerts)


def test_evaluate_hop_alerts_xray_errors() -> None:
    th = HopAlertThresholds(5, 20, 20, 50, 3, 10, 80, 50)
    alerts = evaluate_hop_alerts(
        host="rf3",
        links=[],
        error_delta={"delta_total": 5, "delta_mux_fail": 3, "delta_conn_refused": 1, "delta_retry_exhausted": 1},
        probes=[],
        thresholds=th,
    )
    assert any(a.alert_type == "xray_errors" for a in alerts)


def test_xray_error_log_classify(tmp_path) -> None:
    log_path = tmp_path / "error.log"
    log_path.write_text("", encoding="utf-8")
    state_path = tmp_path / "state"
    tracker = XrayErrorLogTracker()
    tracker.restore_state(state_path, log_path)
    log_path.write_text(
        "2026/06/26 failed to handler mux client\n"
        "2026/06/26 connection refused\n",
        encoding="utf-8",
    )
    d1 = tracker.poll()
    assert d1.delta_mux_fail == 1
    assert d1.delta_conn_refused == 1
    assert d1.delta_total == 2
    d2 = tracker.poll()
    assert d2.delta_total == 0


def test_collect_hop_links_empty() -> None:
    assert hl.collect_hop_links("") == {"enabled": 0, "links": []}
