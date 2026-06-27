"""WG SQLite schema and repository."""

from __future__ import annotations

import json
import sqlite3
import time

COMPONENT = "wg"
CURRENT_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS wg_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  peer_count INTEGER NOT NULL,
  total_rx_bytes INTEGER NOT NULL,
  total_tx_bytes INTEGER NOT NULL,
  stale_peer_count INTEGER NOT NULL,
  peers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wg_samples_ts ON wg_samples(ts);

CREATE TABLE IF NOT EXISTS wg_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  alert_type TEXT NOT NULL,
  alert_key TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wg_alerts_key_ts ON wg_alerts(alert_key, ts);
"""


def migrate_wg_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        """
        INSERT INTO schema_versions (module, version) VALUES ('wg', ?)
        ON CONFLICT(module) DO UPDATE SET version = excluded.version
        """,
        (CURRENT_VERSION,),
    )
    conn.commit()


def insert_sample(
    conn: sqlite3.Connection,
    *,
    ts: int,
    peer_count: int,
    total_rx: int,
    total_tx: int,
    stale_count: int,
    peers_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO wg_samples (
          ts, peer_count, total_rx_bytes, total_tx_bytes, stale_peer_count, peers_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ts, peer_count, total_rx, total_tx, stale_count, peers_json),
    )
    conn.commit()


def last_alert_ts(conn: sqlite3.Connection, alert_key: str, cooldown_sec: int) -> bool:
    """Return True if alert should fire (outside cooldown)."""
    row = conn.execute(
        """
        SELECT ts FROM wg_alerts WHERE alert_key = ? ORDER BY ts DESC LIMIT 1
        """,
        (alert_key,),
    ).fetchone()
    if not row:
        return True
    return (int(time.time()) - int(row[0])) >= cooldown_sec


def record_alert(conn: sqlite3.Connection, *, alert_type: str, alert_key: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO wg_alerts (ts, alert_type, alert_key, message)
        VALUES (?, ?, ?, ?)
        """,
        (int(time.time()), alert_type, alert_key, message),
    )
    conn.commit()


def peers_to_json(peers) -> str:
    payload = [
        {
            "public_key": p.public_key,
            "endpoint": p.endpoint,
            "latest_handshake_sec": p.latest_handshake_sec,
            "transfer_rx": p.transfer_rx,
            "transfer_tx": p.transfer_tx,
        }
        for p in peers
    ]
    return json.dumps(payload, ensure_ascii=False)
