"""Entry module SQLite schema."""

from __future__ import annotations

import json
import sqlite3
import time

COMPONENT = "entry"
CURRENT_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS entry_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  interval_sec INTEGER NOT NULL,
  accepts_json TEXT NOT NULL,
  accepts_primary_rate REAL NOT NULL,
  accepts_secondary_rate REAL NOT NULL,
  accepts_ratio REAL,
  tls_handshake_delta INTEGER NOT NULL,
  io_timeout_delta INTEGER NOT NULL,
  hop_ok INTEGER NOT NULL,
  details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_samples_ts ON entry_samples(ts);

CREATE TABLE IF NOT EXISTS entry_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  alert_type TEXT NOT NULL,
  alert_key TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_alerts_key_ts ON entry_alerts(alert_key, ts);
"""


def migrate_entry_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        """
        INSERT INTO schema_versions (module, version) VALUES ('entry', ?)
        ON CONFLICT(module) DO UPDATE SET version = excluded.version
        """,
        (CURRENT_VERSION,),
    )
    conn.commit()


def insert_sample(
    conn: sqlite3.Connection,
    *,
    ts: int,
    interval_sec: int,
    accepts_by_inbound: dict[str, int],
    accepts_primary_rate: float,
    accepts_secondary_rate: float,
    accepts_ratio: float | None,
    tls_handshake_delta: int,
    io_timeout_delta: int,
    hop_ok: bool,
    details: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO entry_samples (
          ts, interval_sec, accepts_json, accepts_primary_rate, accepts_secondary_rate,
          accepts_ratio, tls_handshake_delta, io_timeout_delta, hop_ok, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            interval_sec,
            json.dumps(accepts_by_inbound, ensure_ascii=False, separators=(",", ":")),
            accepts_primary_rate,
            accepts_secondary_rate,
            accepts_ratio,
            tls_handshake_delta,
            io_timeout_delta,
            1 if hop_ok else 0,
            json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.commit()


def should_alert(conn: sqlite3.Connection, alert_key: str, cooldown_sec: int) -> bool:
    row = conn.execute(
        "SELECT ts FROM entry_alerts WHERE alert_key = ? ORDER BY ts DESC LIMIT 1",
        (alert_key,),
    ).fetchone()
    if not row:
        return True
    return (int(time.time()) - int(row[0])) >= cooldown_sec


def record_alert(conn: sqlite3.Connection, *, alert_type: str, alert_key: str, message: str) -> None:
    conn.execute(
        "INSERT INTO entry_alerts (ts, alert_type, alert_key, message) VALUES (?, ?, ?, ?)",
        (int(time.time()), alert_type, alert_key, message),
    )
    conn.commit()
