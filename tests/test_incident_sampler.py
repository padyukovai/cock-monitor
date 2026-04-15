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
