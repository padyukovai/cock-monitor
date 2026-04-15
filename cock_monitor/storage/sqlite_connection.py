"""Shared SQLite connection factory with consistent policy."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_TIMEOUT_SEC = 60.0
DEFAULT_BUSY_TIMEOUT_MS = 5000


def apply_sqlite_pragmas(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    wal: bool = True,
    synchronous: str = "NORMAL",
) -> None:
    """Apply non-destructive connection pragmas consistently."""
    conn.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))};")
    if wal:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.Error:
            # WAL is best-effort (e.g. read-only database URI).
            pass
    conn.execute(f"PRAGMA synchronous={synchronous};")


def open_sqlite_connection(
    db_path: str | Path,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    uri: bool = False,
    read_only: bool = False,
    wal: bool = True,
) -> sqlite3.Connection:
    """Open SQLite connection with shared timeout/pragma policy."""
    if isinstance(db_path, Path):
        if not uri and not read_only:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        target = str(db_path)
    else:
        target = db_path

    if read_only and not uri:
        target = f"file:{target}?mode=ro"
        uri = True

    conn = sqlite3.connect(target, timeout=timeout_sec, uri=uri)
    apply_sqlite_pragmas(conn, wal=wal)
    return conn
