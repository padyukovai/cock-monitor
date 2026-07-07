"""Tests for /chart PNG time-axis formatting."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from cock_monitor.modules.core.charts import MSK_CHART_TZ, _chart_time_formatter, _fetch_host_leak_rows
from cock_monitor.storage.conntrack_host_repository import (
    ConntrackHostRepository,
    ConntrackSampleInsert,
    HostSampleInsert,
)


def test_chart_time_formatter_uses_msk() -> None:
    fmt = _chart_time_formatter()
    assert fmt.tz == MSK_CHART_TZ


def test_fetch_host_leak_rows_join_does_not_ambiguous_ts() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        with ConntrackHostRepository.open(path) as repo:
            sample = ConntrackSampleInsert(
                ts=1000,
                fill_pct=42,
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
            host = HostSampleInsert(
                ts=1000,
                load1=0.5,
                mem_avail_kb=500_000,
                swap_used_kb=0,
                tcp_inuse=10,
                tcp_orphan=0,
                tcp_tw=0,
                tcp6_inuse=0,
                shaper_rate_mbit=None,
                shaper_cpu_pct=None,
                tc_qdisc_root=None,
                xray_rss_mb=120.0,
                xray_fds=50,
                ss_estab=100,
                ss_time_wait=200,
            )
            repo.insert_sample_and_host(sample, host)
            rows = _fetch_host_leak_rows(repo._conn, 0)
        assert len(rows) == 1
        assert rows[0][0] == 1000
        assert rows[0][6] == 42
    finally:
        path.unlink(missing_ok=True)
