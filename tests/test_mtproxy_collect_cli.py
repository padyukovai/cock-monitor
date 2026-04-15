from __future__ import annotations

import sqlite3
from typing import Protocol

from cock_monitor.mtproxy_collect_cli import dispatch_mtproxy_alerts
from mtproxy_module.alerts import AlertCandidate
from mtproxy_module.repository import can_send_alert, init_schema
from telegram_bot.telegram_client import DeliveryResult


class _ClientLike(Protocol):
    def send_message_with_result(self, _chat_id: str, text: str) -> DeliveryResult: ...


class _FakeClient:
    def __init__(self, results: list[DeliveryResult]) -> None:
        self._results = results
        self.sent_texts: list[str] = []

    def send_message_with_result(self, _chat_id: str, text: str) -> DeliveryResult:
        self.sent_texts.append(text)
        return self._results.pop(0)


def test_dispatch_records_only_successful_alerts() -> None:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    alerts = [
        AlertCandidate(alert_type="warning_ip", alert_key="1.1.1.1", message="warn"),
        AlertCandidate(alert_type="critical_leak", alert_key="global", message="crit"),
    ]
    client: _ClientLike = _FakeClient(
        [
            DeliveryResult(success=False, reason="sendMessage HTTP 500", attempts=3),
            DeliveryResult(success=True, reason="", attempts=2),
        ]
    )

    sent, failed = dispatch_mtproxy_alerts(
        conn=conn,
        client=client,
        chat_id="123",
        alerts=alerts,
    )

    assert sent == 1
    assert failed == 1
    rows = conn.execute("SELECT alert_type, alert_key FROM mtproxy_alerts ORDER BY id").fetchall()
    assert rows == [("critical_leak", "global")]
    assert can_send_alert(conn, "warning_ip", "1.1.1.1", 30) is True
