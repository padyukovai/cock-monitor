from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from mtproxy_module.repository import (
    MTPROXY_SCHEMA_VERSION,
    collect_traffic,
    init_schema,
    record_alert,
    scenario_transaction,
    store_metric,
)

_LEGACY_MTPROXY_DDL = """
CREATE TABLE IF NOT EXISTS mtproxy_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    total_connections INTEGER NOT NULL,
    unique_ips INTEGER NOT NULL,
    bytes_in INTEGER NOT NULL DEFAULT 0,
    bytes_out INTEGER NOT NULL DEFAULT 0,
    top_ips_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_mtproxy_metrics_ts ON mtproxy_metrics(ts);

CREATE TABLE IF NOT EXISTS mtproxy_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    alert_key TEXT NOT NULL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_mtproxy_alerts_ts ON mtproxy_alerts(ts);

CREATE TABLE IF NOT EXISTS mtproxy_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def test_atomic_collect_store_alert_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import mtproxy_module.repository as repo

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    monkeypatch.setattr(repo, "collect_iptables_bytes", lambda _port: (100, 200))

    with scenario_transaction(conn):
        traffic = collect_traffic(conn, 443)
        store_metric(
            conn,
            {"total": 8, "unique_ips": 2, "per_ip": {"1.1.1.1": 5, "2.2.2.2": 3}},
            traffic,
        )
        record_alert(conn, "warning_ip", "1.1.1.1", "warn")

    metric_row = conn.execute(
        "SELECT total_connections, unique_ips, bytes_in, bytes_out FROM mtproxy_metrics"
    ).fetchone()
    assert metric_row == (8, 2, 100, 200)
    alert_row = conn.execute("SELECT alert_type, alert_key FROM mtproxy_alerts").fetchone()
    assert alert_row == ("warning_ip", "1.1.1.1")
    state_rows = dict(conn.execute("SELECT key, value FROM mtproxy_state").fetchall())
    assert state_rows["prev_bytes_in"] == "100"
    assert state_rows["prev_bytes_out"] == "200"


def test_atomic_collect_store_rolls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import mtproxy_module.repository as repo

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    monkeypatch.setattr(repo, "collect_iptables_bytes", lambda _port: (150, 250))

    with pytest.raises(RuntimeError, match="boom"):
        with scenario_transaction(conn):
            traffic = collect_traffic(conn, 443)
            store_metric(conn, {"total": 1, "unique_ips": 1, "per_ip": {}}, traffic)
            record_alert(conn, "down", "global", "should rollback")
            raise RuntimeError("boom")

    assert conn.execute("SELECT COUNT(*) FROM mtproxy_metrics").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM mtproxy_alerts").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM mtproxy_state").fetchone() == (0,)


def test_mtproxy_migrate_empty_db_sets_schema_version() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        conn = sqlite3.connect(str(path))
        init_schema(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "mtproxy_metrics" in tables
        assert "mtproxy_alerts" in tables
        assert "mtproxy_state" in tables
        assert "mtproxy_ip_geo_cache" in tables
        assert _user_version(conn) == MTPROXY_SCHEMA_VERSION
        conn.close()
    finally:
        path.unlink(missing_ok=True)


def test_mtproxy_migrate_legacy_db_preserves_data() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        conn = sqlite3.connect(str(path))
        conn.executescript(_LEGACY_MTPROXY_DDL)
        conn.execute(
            """
            INSERT INTO mtproxy_metrics (ts, total_connections, unique_ips, bytes_in, bytes_out, top_ips_json)
            VALUES (1700000000, 7, 3, 11, 22, '{"1.1.1.1": 7}')
            """
        )
        conn.execute(
            "INSERT INTO mtproxy_state (key, value) VALUES ('prev_bytes_in', '11')"
        )
        conn.commit()
        conn.close()

        conn2 = sqlite3.connect(str(path))
        init_schema(conn2)
        assert _user_version(conn2) == MTPROXY_SCHEMA_VERSION
        row = conn2.execute(
            "SELECT ts, total_connections, unique_ips, bytes_in, bytes_out FROM mtproxy_metrics"
        ).fetchone()
        assert row == (1700000000, 7, 3, 11, 22)
        state = dict(conn2.execute("SELECT key, value FROM mtproxy_state").fetchall())
        assert state["prev_bytes_in"] == "11"
        conn2.close()
    finally:
        path.unlink(missing_ok=True)
