"""METRICS_DB tables and DML for VLESS daily snapshots, checkpoints, and report meta."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cock_monitor.adapters.xui_sqlite import OutboundTrafficRow, TrafficRow, safe_i64


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_outbound_snapshots (
            snapshot_day_msk TEXT NOT NULL,
            ts INTEGER NOT NULL,
            tag TEXT NOT NULL,
            up_bytes INTEGER NOT NULL,
            down_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            PRIMARY KEY (snapshot_day_msk, tag)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_outbound_snapshots_ts
        ON vless_outbound_snapshots(ts)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_outbound_checkpoints (
            ts INTEGER NOT NULL,
            tag TEXT NOT NULL,
            up_bytes INTEGER NOT NULL,
            down_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (ts, tag)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_outbound_checkpoints_ts
        ON vless_outbound_checkpoints(ts)
        """
    )


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
        """,
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


def upsert_outbound_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_day_msk: str,
    ts: int,
    rows: list[OutboundTrafficRow],
) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO vless_outbound_snapshots (
            snapshot_day_msk, ts, tag, up_bytes, down_bytes, total_bytes
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_day_msk, tag) DO UPDATE SET
            ts = excluded.ts,
            up_bytes = excluded.up_bytes,
            down_bytes = excluded.down_bytes,
            total_bytes = excluded.total_bytes
        """,
        [(snapshot_day_msk, ts, r.tag, r.up, r.down, r.total) for r in rows],
    )


def get_outbound_snapshot_maps(
    conn: sqlite3.Connection,
    day_msk: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    cur = conn.execute(
        """
        SELECT tag, up_bytes, down_bytes, total_bytes
        FROM vless_outbound_snapshots
        WHERE snapshot_day_msk = ?
        """,
        (day_msk,),
    )
    up_map: dict[str, int] = {}
    down_map: dict[str, int] = {}
    total_map: dict[str, int] = {}
    for tag, up, down, total in cur.fetchall():
        key = str(tag)
        up_map[key] = safe_i64(up)
        down_map[key] = safe_i64(down)
        total_map[key] = safe_i64(total)
    return up_map, down_map, total_map


def get_outbound_checkpoint_maps(
    conn: sqlite3.Connection,
    ts: int,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    cur = conn.execute(
        """
        SELECT tag, up_bytes, down_bytes, total_bytes
        FROM vless_outbound_checkpoints
        WHERE ts = ?
        """,
        (ts,),
    )
    up_map: dict[str, int] = {}
    down_map: dict[str, int] = {}
    total_map: dict[str, int] = {}
    for tag, up, down, total in cur.fetchall():
        key = str(tag)
        up_map[key] = safe_i64(up)
        down_map[key] = safe_i64(down)
        total_map[key] = safe_i64(total)
    return up_map, down_map, total_map


def save_outbound_checkpoint(
    conn: sqlite3.Connection,
    *,
    ts: int,
    rows: list[OutboundTrafficRow],
    source: str,
) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO vless_outbound_checkpoints (
            ts, tag, up_bytes, down_bytes, total_bytes, source
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(ts, r.tag, r.up, r.down, r.total, source) for r in rows],
    )


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


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = True) -> Iterator[None]:
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
