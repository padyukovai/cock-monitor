from __future__ import annotations

import sqlite3
from pathlib import Path

from cock_monitor.storage.sqlite_connection import open_sqlite_connection


def test_open_sqlite_connection_applies_busy_timeout_and_wal(tmp_path: Path) -> None:
    db = tmp_path / "policy.db"
    conn = open_sqlite_connection(db)
    try:
        busy = conn.execute("PRAGMA busy_timeout").fetchone()
        mode = conn.execute("PRAGMA journal_mode").fetchone()
        assert busy is not None and int(busy[0]) == 5000
        assert mode is not None and str(mode[0]).lower() in {"wal", "memory"}
    finally:
        conn.close()


def test_open_sqlite_connection_read_only_works(tmp_path: Path) -> None:
    db = tmp_path / "ro.db"
    setup = sqlite3.connect(str(db))
    setup.execute("CREATE TABLE t(v INTEGER)")
    setup.execute("INSERT INTO t(v) VALUES (1)")
    setup.commit()
    setup.close()

    conn = open_sqlite_connection(db, read_only=True, wal=False)
    try:
        row = conn.execute("SELECT v FROM t").fetchone()
        assert row == (1,)
    finally:
        conn.close()
