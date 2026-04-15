"""
Versioned schema for conntrack_samples + host_samples in METRICS_DB.

Uses table cock_monitor_schema(component, version) so multiple subsystems can
share one database file without colliding on PRAGMA user_version.

Component name: conntrack_host. Version 1 matches historical DDL from check-conntrack.sh.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

COMPONENT = "conntrack_host"
CURRENT_VERSION = 1

_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS cock_monitor_schema (
  component TEXT PRIMARY KEY NOT NULL,
  version INTEGER NOT NULL
);
"""

_V1_DDL = """
CREATE TABLE IF NOT EXISTS conntrack_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  fill_pct INTEGER,
  fill_count INTEGER,
  fill_max INTEGER,
  "drop" INTEGER NOT NULL DEFAULT 0,
  insert_failed INTEGER NOT NULL DEFAULT 0,
  early_drop INTEGER NOT NULL DEFAULT 0,
  "error" INTEGER NOT NULL DEFAULT 0,
  invalid INTEGER NOT NULL DEFAULT 0,
  search_restart INTEGER NOT NULL DEFAULT 0,
  interval_sec INTEGER,
  delta_drop INTEGER,
  delta_insert_failed INTEGER,
  delta_early_drop INTEGER,
  delta_error INTEGER,
  delta_invalid INTEGER,
  delta_search_restart INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON conntrack_samples(ts);
CREATE TABLE IF NOT EXISTS host_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  load1 REAL,
  mem_avail_kb INTEGER,
  swap_used_kb INTEGER,
  tcp_inuse INTEGER,
  tcp_orphan INTEGER,
  tcp_tw INTEGER,
  tcp6_inuse INTEGER,
  shaper_rate_mbit REAL,
  shaper_cpu_pct INTEGER,
  tc_qdisc_root TEXT
);
CREATE INDEX IF NOT EXISTS idx_host_samples_ts ON host_samples(ts);
"""


def _get_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT version FROM cock_monitor_schema WHERE component = ?",
        (COMPONENT,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        """
        INSERT INTO cock_monitor_schema (component, version)
        VALUES (?, ?)
        ON CONFLICT(component) DO UPDATE SET version = excluded.version
        """,
        (COMPONENT, version),
    )


def _apply_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_V1_DDL)


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _apply_v1),
]


def migrate_conntrack_host(conn: sqlite3.Connection) -> None:
    """Apply pending migrations for COMPONENT up to CURRENT_VERSION."""
    conn.executescript(_REGISTRY_DDL)
    v = _get_version(conn)
    for target, fn in MIGRATIONS:
        if v < target:
            fn(conn)
            _set_version(conn, target)
            v = target
    if v < CURRENT_VERSION:
        raise RuntimeError(
            f"{COMPONENT}: schema version {v} < expected {CURRENT_VERSION}"
        )
    conn.commit()
