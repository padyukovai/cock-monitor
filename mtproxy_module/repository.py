from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from mtproxy_module.collector import collect_iptables_bytes
from mtproxy_module.config import MtproxyConfig, to_int


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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

        CREATE TABLE IF NOT EXISTS mtproxy_ip_geo_cache (
            ip TEXT PRIMARY KEY,
            data TEXT,
            ts INTEGER
        );
        """
    )
    conn.commit()


def _state_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM mtproxy_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _state_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO mtproxy_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def load_thresholds(conn: sqlite3.Connection, cfg: MtproxyConfig) -> tuple[int, int]:
    warn = to_int(_state_get(conn, "threshold_max_connections_per_ip"), cfg.max_connections_per_ip)
    crit = to_int(_state_get(conn, "threshold_max_unique_ips"), cfg.max_unique_ips)
    return max(1, warn), max(1, crit)


def update_threshold(conn: sqlite3.Connection, param: str, value: int) -> str:
    if value <= 0:
        return "Invalid value. Must be a positive integer."
    if param in {"warning", "max_connections_per_ip"}:
        _state_set(conn, "threshold_max_connections_per_ip", str(value))
        return f"MTPROXY threshold 'max_connections_per_ip' updated to {value}."
    if param in {"critical", "max_unique_ips"}:
        _state_set(conn, "threshold_max_unique_ips", str(value))
        return f"MTPROXY threshold 'max_unique_ips' updated to {value}."
    return "Unknown parameter. Use warning|critical."


def collect_traffic(conn: sqlite3.Connection, port: int) -> dict[str, int]:
    current_in, current_out = collect_iptables_bytes(port)
    prev_in = to_int(_state_get(conn, "prev_bytes_in"), 0)
    prev_out = to_int(_state_get(conn, "prev_bytes_out"), 0)
    delta_in = current_in - prev_in if current_in >= prev_in else current_in
    delta_out = current_out - prev_out if current_out >= prev_out else current_out
    _state_set(conn, "prev_bytes_in", str(current_in))
    _state_set(conn, "prev_bytes_out", str(current_out))
    return {"bytes_in": max(0, delta_in), "bytes_out": max(0, delta_out)}


def store_metric(conn: sqlite3.Connection, conns: dict[str, Any], traffic: dict[str, int]) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO mtproxy_metrics (ts, total_connections, unique_ips, bytes_in, bytes_out, top_ips_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            int(conns.get("total", 0)),
            int(conns.get("unique_ips", 0)),
            int(traffic.get("bytes_in", 0)),
            int(traffic.get("bytes_out", 0)),
            json.dumps(conns.get("per_ip", {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def can_send_alert(conn: sqlite3.Connection, alert_type: str, alert_key: str, cooldown_min: int) -> bool:
    now = int(time.time())
    row = conn.execute(
        "SELECT ts FROM mtproxy_alerts WHERE alert_type = ? AND alert_key = ? ORDER BY ts DESC LIMIT 1",
        (alert_type, alert_key),
    ).fetchone()
    if row is None:
        return True
    return (now - int(row[0])) >= max(0, cooldown_min) * 60


def record_alert(conn: sqlite3.Connection, alert_type: str, alert_key: str, message: str) -> None:
    conn.execute(
        "INSERT INTO mtproxy_alerts (ts, alert_type, alert_key, message) VALUES (?, ?, ?, ?)",
        (int(time.time()), alert_type, alert_key, message),
    )
    conn.commit()


def summary_rows(conn: sqlite3.Connection, start_ts: int) -> list[tuple]:
    return list(
        conn.execute(
            """
            SELECT ts, total_connections, unique_ips, bytes_in, bytes_out, top_ips_json
            FROM mtproxy_metrics
            WHERE ts > ?
            ORDER BY ts
            """,
            (start_ts,),
        ).fetchall()
    )
