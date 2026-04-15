"""Tests for conntrack_samples / host_samples storage and migrations."""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from cock_monitor.storage.conntrack_host_repository import (
    ConntrackHostRepository,
    ConntrackSampleInsert,
    HostSampleInsert,
)
from cock_monitor.storage.migrations_conntrack_host import (
    COMPONENT,
    CURRENT_VERSION,
    migrate_conntrack_host,
)

_LEGACY_DDL = """
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


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM cock_monitor_schema WHERE component = ?",
        (COMPONENT,),
    ).fetchone()
    return int(row[0]) if row else 0


def test_migrate_empty_db_creates_tables_and_version() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        with ConntrackHostRepository.open(path) as repo:
            assert repo.read_last_stats_line() is None
        conn = sqlite3.connect(str(path))
        try:
            assert _schema_version(conn) == CURRENT_VERSION
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "conntrack_samples" in tables
            assert "host_samples" in tables
            assert "cock_monitor_schema" in tables
        finally:
            conn.close()
    finally:
        path.unlink(missing_ok=True)


def test_migrate_legacy_db_without_registry() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        conn = sqlite3.connect(str(path))
        conn.executescript(_LEGACY_DDL)
        conn.execute(
            """
            INSERT INTO conntrack_samples (
              ts, fill_pct, fill_count, fill_max,
              "drop", insert_failed, early_drop, "error", invalid, search_restart,
              interval_sec, delta_drop, delta_insert_failed, delta_early_drop,
              delta_error, delta_invalid, delta_search_restart
            ) VALUES (1700000000, 50, 100, 200, 1, 2, 3, 4, 5, 6, 60, 0, 0, 0, 0, 0, 0)
            """
        )
        conn.execute(
            """
            INSERT INTO host_samples (
              ts, load1, mem_avail_kb, swap_used_kb,
              tcp_inuse, tcp_orphan, tcp_tw, tcp6_inuse,
              shaper_rate_mbit, shaper_cpu_pct, tc_qdisc_root
            ) VALUES (1700000000, 0.5, 1000, 0, 10, 0, 0, 0, NULL, NULL, NULL)
            """
        )
        conn.commit()
        conn.close()

        conn2 = sqlite3.connect(str(path))
        migrate_conntrack_host(conn2)
        assert _schema_version(conn2) == CURRENT_VERSION
        row = conn2.execute(
            "SELECT ts, fill_pct FROM conntrack_samples WHERE ts = 1700000000"
        ).fetchone()
        assert row == (1700000000, 50)
        migrate_conntrack_host(conn2)
        assert _schema_version(conn2) == CURRENT_VERSION
        conn2.close()
    finally:
        path.unlink(missing_ok=True)


def test_insert_retention_trim_orphans() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        with ConntrackHostRepository.open(path) as repo:
            s = ConntrackSampleInsert(
                ts=1000,
                fill_pct=10,
                fill_count=1,
                fill_max=2,
                drop=0,
                insert_failed=0,
                early_drop=0,
                error=0,
                invalid=0,
                search_restart=0,
                interval_sec=None,
                delta_drop=None,
                delta_insert_failed=None,
                delta_early_drop=None,
                delta_error=None,
                delta_invalid=None,
                delta_search_restart=None,
            )
            h = HostSampleInsert(
                ts=1000,
                load1=None,
                mem_avail_kb=None,
                swap_used_kb=None,
                tcp_inuse=None,
                tcp_orphan=None,
                tcp_tw=None,
                tcp6_inuse=None,
                shaper_rate_mbit=None,
                shaper_cpu_pct=None,
                tc_qdisc_root=None,
            )
            repo.insert_sample_and_host(s, h)
            repo.insert_sample_and_host(
                replace(s, ts=2000, fill_pct=11), replace(h, ts=2000)
            )
            repo.insert_sample_and_host(
                replace(s, ts=5000, fill_pct=12), replace(h, ts=5000)
            )

            # DELETE WHERE ts < cutoff — keep rows with ts >= cutoff
            repo.apply_retention(1500)
            ids = [r[0] for r in repo._conn.execute("SELECT ts FROM conntrack_samples ORDER BY ts").fetchall()]
            assert ids == [2000, 5000]

            repo.insert_sample_and_host(
                replace(s, ts=6000, fill_pct=1),
                replace(h, ts=6000),
            )
            repo.trim_to_max_rows(2)
            repo.delete_host_orphans()
            ts_left = [r[0] for r in repo._conn.execute("SELECT ts FROM conntrack_samples ORDER BY ts").fetchall()]
            assert ts_left == [5000, 6000]
            host_ts = [r[0] for r in repo._conn.execute("SELECT ts FROM host_samples ORDER BY ts").fetchall()]
            assert host_ts == [5000, 6000]

            line = repo.read_last_stats_line()
            assert line is not None
            parts = line.split("|")
            assert parts[0] == "6000"
    finally:
        path.unlink(missing_ok=True)


def test_write_from_env_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cock_monitor.conntrack_storage_cli import _write_from_env

    db = tmp_path / "m.db"
    monkeypatch.setenv("COCK_MS_DB", str(db))
    monkeypatch.setenv("COCK_MS_NOW_TS", "9000")
    monkeypatch.setenv("COCK_MS_HAS_CT", "0")
    monkeypatch.setenv("COCK_MS_RETENTION_DAYS", "0")
    monkeypatch.setenv("COCK_MS_MAX_ROWS", "0")
    monkeypatch.setenv("COCK_MS_RETENTION_NOW_TS", "9000")
    monkeypatch.setenv("COCK_MS_FILL_PCT", "")
    monkeypatch.setenv("COCK_MS_FILL_COUNT", "")
    monkeypatch.setenv("COCK_MS_FILL_MAX", "")
    monkeypatch.setenv("COCK_MS_HOST_LOAD1", "1.5")
    monkeypatch.setenv("COCK_MS_HOST_MEM_AVAIL_KB", "4096")
    monkeypatch.delenv("COCK_MS_INTERVAL_SEC", raising=False)
    assert _write_from_env(db) == 0
    with ConntrackHostRepository.open(db) as repo:
        line = repo.read_last_stats_line()
        assert line is not None
        assert line.startswith("9000|0|")
