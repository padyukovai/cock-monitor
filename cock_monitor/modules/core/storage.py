"""Core SQLite schema (conntrack_samples + host_samples)."""

from __future__ import annotations

import sqlite3

from cock_monitor.storage.migrations_conntrack_host import migrate_conntrack_host


def migrate_core_schema(conn: sqlite3.Connection) -> None:
    migrate_conntrack_host(conn)
