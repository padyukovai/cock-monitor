from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from cock_monitor.modules.mtproxy.alerts import AlertCandidate
from cock_monitor.modules.mtproxy.repository import can_send_alert, init_schema
from cock_monitor.mtproxy_collect_cli import dispatch_mtproxy_alerts, run
from cock_monitor.platform.telegram.telegram_client import DeliveryResult


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


def test_run_stores_metrics_without_telegram_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / "cock-monitor.env"
    db_path = tmp_path / "metrics.db"
    env_file.write_text(
        "\n".join(
            [
                "ENABLED_MODULES=core,mtproxy",
                "MTPROXY_PORT=8443",
                f"METRICS_DB={db_path}",
            ]
        ),
        encoding="utf-8",
    )

    assert run(["--env-file", str(env_file)]) == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT total_connections, unique_ips FROM mtproxy_metrics").fetchone()
    conn.close()
    assert row is not None
    assert row[0] >= 0
    assert row[1] >= 0
