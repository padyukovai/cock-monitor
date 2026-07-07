"""Tests for leak diagnostics schema v2 and alert logic."""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

from cock_monitor.modules.core.leak_alert import LeakAlertConfig, evaluate_leak_rows
from cock_monitor.modules.incident.leak_profile import build_leak_investigation_report
from cock_monitor.storage.conntrack_host_repository import (
    ConntrackHostRepository,
    ConntrackSampleInsert,
    HostSampleInsert,
)
from cock_monitor.storage.migrations_conntrack_host import CURRENT_VERSION, migrate_conntrack_host


def _sample(ts: int, fill_pct: int = 10) -> ConntrackSampleInsert:
    return ConntrackSampleInsert(
        ts=ts,
        fill_pct=fill_pct,
        fill_count=1,
        fill_max=10,
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


def _host(ts: int, **kwargs) -> HostSampleInsert:
    base = HostSampleInsert(
        ts=ts,
        load1=0.5,
        mem_avail_kb=500_000,
        swap_used_kb=0,
        tcp_inuse=10,
        tcp_orphan=0,
        tcp_tw=100,
        tcp6_inuse=0,
        shaper_rate_mbit=None,
        shaper_cpu_pct=None,
        tc_qdisc_root=None,
    )
    return replace(base, **kwargs)


def test_migrate_v2_adds_leak_columns() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        conn = sqlite3.connect(str(path))
        migrate_conntrack_host(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(host_samples)")}
        assert "xray_rss_mb" in cols
        assert "ss_estab" in cols
        row = conn.execute(
            "SELECT version FROM cock_monitor_schema WHERE component = 'conntrack_host'"
        ).fetchone()
        assert int(row[0]) == CURRENT_VERSION
        conn.close()
    finally:
        path.unlink(missing_ok=True)


def test_host_leak_rows_roundtrip() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        with ConntrackHostRepository.open(path) as repo:
            repo.insert_sample_and_host(
                _sample(1000),
                _host(
                    1000,
                    xray_rss_mb=120.5,
                    xray_fds=42,
                    ss_estab=300,
                    ss_time_wait=800,
                ),
            )
            rows = repo.fetch_host_leak_rows(0)
            assert len(rows) == 1
            assert rows[0][2] == 120.5
            assert rows[0][3] == 42
            assert rows[0][5] == 300
            assert rows[0][6] == 800
    finally:
        path.unlink(missing_ok=True)


def test_evaluate_leak_rows_rss_trend() -> None:
    cfg = LeakAlertConfig(
        enabled=True,
        cooldown_sec=60,
        dry_run=True,
        bot_token="",
        chat_id="",
        proxy_url=None,
        state_file=Path("/tmp/x"),
        metrics_db=Path("/tmp/y"),
        rss_warn_mb=200,
        rss_crit_mb=500,
        rss_trend_window_hours=6,
        rss_trend_min_mb=50,
        fds_warn=500,
        fds_trend_min=100,
        conntrack_fill_warn_pct=70,
    )
    rows = [
        (1000, 600_000, 100.0, 50, 0.0, 200, 500, 0, 0),
        (2000, 550_000, 150.0, 80, 0.0, 250, 700, 0, 0),
        (3000, 500_000, 200.0, 120, 0.0, 300, 900, 0, 0),
    ]
    verdict = evaluate_leak_rows(rows, cfg=cfg)
    assert verdict.fire is True
    assert verdict.severity >= 1


def test_build_leak_investigation_report_from_jsonl(tmp_path: Path) -> None:
    import json

    log = tmp_path / "leak-investigation-20260707.jsonl"
    for i, rss in enumerate((200.0, 250.0, 320.0)):
        row = {
            "ts_epoch": 1_000_000 + i * 3600,
            "mem_avail_kb": 500_000 - i * 30_000,
            "leak_profile": {
                "xray": {"rss_mb": rss},
                "conntrack": {"fill_pct": 40 + i * 5},
            },
            "tcp": {"time_wait": 1000 + i * 200},
        }
        log.write_text(
            (log.read_text(encoding="utf-8") if log.exists() else "")
            + json.dumps(row)
            + "\n",
            encoding="utf-8",
        )
    body = build_leak_investigation_report(
        host="rf3",
        start_ts=999_000,
        end_ts=1_010_000,
        log_dir=tmp_path,
    )
    assert "xray RSS" in body
    assert "Hypotheses" in body
