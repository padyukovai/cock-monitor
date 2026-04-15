"""METRICS_DB tables and DML for VLESS daily snapshots, checkpoints, and report meta."""
from __future__ import annotations

import sqlite3

from cock_monitor.adapters.xui_sqlite import TrafficRow, safe_i64


def ensure_report_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_daily_snapshots (
            snapshot_day_msk TEXT NOT NULL,
            ts INTEGER NOT NULL,
            email TEXT NOT NULL,
            up_bytes INTEGER NOT NULL,
            down_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            PRIMARY KEY (snapshot_day_msk, email)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_daily_snapshots_ts
        ON vless_daily_snapshots(ts)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_daily_reports (
            snapshot_day_msk TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            total_clients INTEGER NOT NULL,
            total_delta_bytes INTEGER NOT NULL,
            top1_email TEXT NOT NULL,
            top1_delta_bytes INTEGER NOT NULL,
            sent_ok INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_report_checkpoints (
            ts INTEGER NOT NULL,
            email TEXT NOT NULL,
            total_bytes INTEGER NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (ts, email)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_report_checkpoints_ts
        ON vless_report_checkpoints(ts)
        """
    )
    conn.commit()


def upsert_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_day_msk: str,
    ts: int,
    rows: list[TrafficRow],
) -> None:
    conn.executemany(
        """
        INSERT INTO vless_daily_snapshots (
            snapshot_day_msk, ts, email, up_bytes, down_bytes, total_bytes
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_day_msk, email) DO UPDATE SET
            ts = excluded.ts,
            up_bytes = excluded.up_bytes,
            down_bytes = excluded.down_bytes,
            total_bytes = excluded.total_bytes
        """,
        [(snapshot_day_msk, ts, r.email, r.up, r.down, r.total) for r in rows],
    )
    conn.commit()


def get_snapshot_map(conn: sqlite3.Connection, day_msk: str) -> dict[str, int]:
    cur = conn.execute(
        """
        SELECT email, total_bytes
        FROM vless_daily_snapshots
        WHERE snapshot_day_msk = ?
        """,
        (day_msk,),
    )
    out: dict[str, int] = {}
    for email, total in cur.fetchall():
        out[str(email)] = safe_i64(total)
    return out


def get_last_sent_checkpoint_ts(conn: sqlite3.Connection, *, source: str) -> int | None:
    cur = conn.execute(
        """
        SELECT MAX(ts)
        FROM vless_report_checkpoints
        WHERE source = ?
        """
        ,
        (source,),
    )
    row = cur.fetchone()
    if not row:
        return None
    value = row[0]
    if value is None:
        return None
    return safe_i64(value)


def get_checkpoint_map(conn: sqlite3.Connection, ts: int) -> dict[str, int]:
    cur = conn.execute(
        """
        SELECT email, total_bytes
        FROM vless_report_checkpoints
        WHERE ts = ?
        """,
        (ts,),
    )
    out: dict[str, int] = {}
    for email, total in cur.fetchall():
        out[str(email)] = safe_i64(total)
    return out


def save_checkpoint(
    conn: sqlite3.Connection,
    *,
    ts: int,
    rows: list[TrafficRow],
    source: str,
) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO vless_report_checkpoints (ts, email, total_bytes, source)
        VALUES (?, ?, ?, ?)
        """,
        [(ts, r.email, r.total, source) for r in rows],
    )
    conn.commit()


def save_report_meta(
    conn: sqlite3.Connection,
    *,
    snapshot_day_msk: str,
    ts: int,
    total_clients: int,
    total_delta_bytes: int,
    top1_email: str,
    top1_delta_bytes: int,
    sent_ok: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO vless_daily_reports (
            snapshot_day_msk, ts, total_clients, total_delta_bytes,
            top1_email, top1_delta_bytes, sent_ok
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_day_msk) DO UPDATE SET
            ts = excluded.ts,
            total_clients = excluded.total_clients,
            total_delta_bytes = excluded.total_delta_bytes,
            top1_email = excluded.top1_email,
            top1_delta_bytes = excluded.top1_delta_bytes,
            sent_ok = excluded.sent_ok
        """,
        (
            snapshot_day_msk,
            ts,
            total_clients,
            total_delta_bytes,
            top1_email,
            top1_delta_bytes,
            1 if sent_ok else 0,
        ),
    )
    conn.commit()
