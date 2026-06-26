"""Hop module SQLite schema."""

from __future__ import annotations

import json
import sqlite3
import time

COMPONENT = "hop"
CURRENT_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS hop_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  link_name TEXT NOT NULL,
  estab INTEGER NOT NULL,
  fin_wait INTEGER NOT NULL,
  time_wait INTEGER NOT NULL,
  link_error TEXT NOT NULL,
  error_delta_total INTEGER NOT NULL,
  error_delta_mux INTEGER NOT NULL,
  error_delta_refused INTEGER NOT NULL,
  error_delta_retry INTEGER NOT NULL,
  probe_ok INTEGER,
  probe_total INTEGER,
  probe_latency_p50_ms INTEGER,
  details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hop_samples_ts ON hop_samples(ts);
CREATE INDEX IF NOT EXISTS idx_hop_samples_name_ts ON hop_samples(link_name, ts);

CREATE TABLE IF NOT EXISTS hop_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  alert_type TEXT NOT NULL,
  alert_key TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hop_alerts_key_ts ON hop_alerts(alert_key, ts);
"""


def migrate_hop_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        """
        INSERT INTO schema_versions (module, version) VALUES ('hop', ?)
        ON CONFLICT(module) DO UPDATE SET version = excluded.version
        """,
        (CURRENT_VERSION,),
    )
    conn.commit()


def insert_sample(
    conn: sqlite3.Connection,
    *,
    ts: int,
    link_name: str,
    estab: int,
    fin_wait: int,
    time_wait: int,
    link_error: str,
    error_delta_total: int,
    error_delta_mux: int,
    error_delta_refused: int,
    error_delta_retry: int,
    probe_ok: int | None,
    probe_total: int | None,
    probe_latency_p50_ms: int | None,
    details: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO hop_samples (
          ts, link_name, estab, fin_wait, time_wait, link_error,
          error_delta_total, error_delta_mux, error_delta_refused, error_delta_retry,
          probe_ok, probe_total, probe_latency_p50_ms, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            link_name,
            estab,
            fin_wait,
            time_wait,
            link_error,
            error_delta_total,
            error_delta_mux,
            error_delta_refused,
            error_delta_retry,
            probe_ok,
            probe_total,
            probe_latency_p50_ms,
            json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.commit()


def should_alert(conn: sqlite3.Connection, alert_key: str, cooldown_sec: int) -> bool:
    row = conn.execute(
        "SELECT ts FROM hop_alerts WHERE alert_key = ? ORDER BY ts DESC LIMIT 1",
        (alert_key,),
    ).fetchone()
    if not row:
        return True
    return (int(time.time()) - int(row[0])) >= cooldown_sec


def record_alert(conn: sqlite3.Connection, *, alert_type: str, alert_key: str, message: str) -> None:
    conn.execute(
        "INSERT INTO hop_alerts (ts, alert_type, alert_key, message) VALUES (?, ?, ?, ?)",
        (int(time.time()), alert_type, alert_key, message),
    )
    conn.commit()
