"""Unit tests for MTProxy alert cooldown (SQLite)."""

from __future__ import annotations

import sqlite3

import pytest

from mtproxy_module.repository import can_send_alert, init_schema, record_alert


@pytest.fixture
def memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def test_can_send_alert_first_time(memory_conn: sqlite3.Connection) -> None:
    assert can_send_alert(memory_conn, "warning_ip", "1.2.3.4", 30) is True


def test_cooldown_blocks_repeat_within_window(memory_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    import mtproxy_module.repository as repo

    t0 = 1_700_000_000
    monkeypatch.setattr(repo.time, "time", lambda: float(t0))
    assert can_send_alert(memory_conn, "down", "global", 30) is True
    record_alert(memory_conn, "down", "global", "msg")
    monkeypatch.setattr(repo.time, "time", lambda: float(t0 + 29 * 60))
    assert can_send_alert(memory_conn, "down", "global", 30) is False


def test_cooldown_allows_after_window(memory_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    import mtproxy_module.repository as repo

    t0 = 1_700_000_000
    monkeypatch.setattr(repo.time, "time", lambda: float(t0))
    record_alert(memory_conn, "down", "global", "msg")
    monkeypatch.setattr(repo.time, "time", lambda: float(t0 + 31 * 60))
    assert can_send_alert(memory_conn, "down", "global", 30) is True
