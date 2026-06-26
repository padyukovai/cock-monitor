"""Pure helpers from cock_monitor.services.incident_sampler."""
from __future__ import annotations

import pytest
from cock_monitor.services import incident_sampler as ismp
from telegram_bot.telegram_client import DeliveryResult


def test_parse_ping_output_linux_style() -> None:
    text = """
PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=56 time=10.1 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1002ms
rtt min/avg/max/mdev = 10.100/10.200/10.300/0.050 ms
"""
    tx, rx, loss, avg = ismp.parse_ping_output(text)
    assert tx == 2 and rx == 2 and loss == 0
    assert abs(avg - 10.2) < 0.01


def test_compute_level_conntrack_crit() -> None:
    assert (
        ismp.compute_level(
            fill_pct=96,
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
        )
        == "CRIT"
    )


def test_parse_hop_link_spec() -> None:
    assert ismp.parse_hop_link_spec("germany:dst:144.31.154.44:10089") == {
        "name": "germany",
        "mode": "dst",
        "host": "144.31.154.44",
        "port": 10089,
    }
    assert ismp.parse_hop_link_spec("rf3-de:sport::10089") == {
        "name": "rf3-de",
        "mode": "sport",
        "host": "",
        "port": 10089,
    }
    assert ismp.parse_hop_link_spec("bad:spec") is None


def test_parse_hop_links_env() -> None:
    links = ismp.parse_hop_links_env(
        "germany:dst:144.31.154.44:10089,usa:dst:153.75.246.28:10090"
    )
    assert len(links) == 2
    assert links[0]["name"] == "germany"
    assert links[1]["port"] == 10090


def test_compute_level_hop_fin_wait_warn() -> None:
    assert (
        ismp.compute_level(
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
            hop_links=[{"name": "germany", "estab": 1, "fin_wait": 25}],
            hop_estab_warn=5,
            hop_estab_crit=20,
            hop_fin_wait_warn=20,
            hop_fin_wait_crit=50,
        )
        == "WARN"
    )


def test_compute_level_hop_estab_crit() -> None:
    assert (
        ismp.compute_level(
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
            hop_links=[{"name": "germany", "estab": 30, "fin_wait": 0}],
            hop_estab_warn=5,
            hop_estab_crit=20,
            hop_fin_wait_warn=20,
            hop_fin_wait_crit=50,
        )
        == "CRIT"
    )


def test_compute_level_hop_error_warn() -> None:
    assert (
        ismp.compute_level(
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
            hop_links=[{"name": "germany", "estab": 0, "fin_wait": 0, "error": "ss_rc_1"}],
            hop_estab_warn=5,
            hop_estab_crit=20,
            hop_fin_wait_warn=20,
            hop_fin_wait_crit=50,
        )
        == "WARN"
    )


def test_compute_level_hop_error_does_not_mask_crit() -> None:
    assert (
        ismp.compute_level(
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
            hop_links=[{"name": "germany", "estab": 30, "fin_wait": 0, "error": "ss_rc_1"}],
            hop_estab_warn=5,
            hop_estab_crit=20,
            hop_fin_wait_warn=20,
            hop_fin_wait_crit=50,
        )
        == "CRIT"
    )


def test_compute_level_tcp_fin_wait_warn() -> None:
    assert (
        ismp.compute_level(
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
            tcp_fin_wait=60,
            tcp_fin_wait_warn=50,
        )
        == "WARN"
    )


def test_safe_pct_import() -> None:
    from cock_monitor.adapters.linux_host import safe_pct

    assert safe_pct(17, 20) == 85


def test_maybe_alert_sets_cooldown_only_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    st = {"last_level": "WARN", "last_alert_ts": "100"}
    monkeypatch.setenv("INCIDENT_ALERT_ENABLE", "1")
    monkeypatch.setenv("INCIDENT_ALERT_COOLDOWN_SEC", "10")
    monkeypatch.setattr(
        ismp,
        "send_telegram",
        lambda _text, parse_mode=None: DeliveryResult(success=False, reason="HTTP 500", attempts=3),
    )

    ismp.maybe_alert(120, "WARN", st, snapshot_text="snapshot")
    assert st["last_alert_ts"] == "100"

    monkeypatch.setattr(
        ismp,
        "send_telegram",
        lambda _text, parse_mode=None: DeliveryResult(success=True, reason="", attempts=1),
    )
    ismp.maybe_alert(130, "WARN", st, snapshot_text="snapshot")
    assert st["last_alert_ts"] == "130"


def test_maybe_alert_requires_consecutive_warn_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = {"count": 0}
    st = {"last_level": "OK", "last_alert_ts": "0", "non_ok_streak": "1"}
    monkeypatch.setenv("INCIDENT_ALERT_ENABLE", "1")
    monkeypatch.setenv("INCIDENT_WARN_CONSECUTIVE", "2")

    def _send(_text: str, parse_mode: str | None = None) -> DeliveryResult:
        sent["count"] += 1
        return DeliveryResult(success=True, reason="", attempts=1)

    monkeypatch.setattr(ismp, "send_telegram", _send)

    ismp.maybe_alert(100, "WARN", st, snapshot_text="snapshot")
    assert sent["count"] == 0

    st["non_ok_streak"] = "2"
    ismp.maybe_alert(110, "WARN", st, snapshot_text="snapshot")
    assert sent["count"] == 1


def test_incident_track_requires_two_consecutive_warn_for_postmortem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCIDENT_WARN_CONSECUTIVE", "2")
    monkeypatch.setenv("INCIDENT_POSTMORTEM_ENABLE", "1")
    sent = {"count": 0}

    def _send(_text: str, parse_mode: str | None = None) -> DeliveryResult:
        sent["count"] += 1
        return DeliveryResult(success=True, reason="", attempts=1)

    monkeypatch.setattr(ismp, "send_telegram", _send)
    st = {
        "last_level": "OK",
        "last_alert_ts": "0",
        "dns_fail_streak": "0",
        "incident_active": "0",
        "incident_start_ts": "0",
        "incident_peak_level": "OK",
        "non_ok_streak": "0",
        "non_ok_first_ts": "0",
        "non_ok_peak_level": "OK",
    }

    ismp.incident_track_and_postmortem("OK", "WARN", 100, "host", st, ismp.Path("/tmp"))
    assert st["incident_active"] == "0"
    assert st["non_ok_streak"] == "1"

    ismp.incident_track_and_postmortem("WARN", "OK", 110, "host", st, ismp.Path("/tmp"))
    assert st["incident_active"] == "0"
    assert sent["count"] == 0
