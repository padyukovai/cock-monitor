"""Unit tests for conntrack alert policy (ported from check-conntrack.sh)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cock_monitor.domain.conntrack_policy import (
    evaluate_stats_alert,
    metrics_phase_result,
    severity_from_fill_pct,
    should_send_fill_alert,
    should_send_stats_alert,
    u32_counter_delta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_severity_from_fill_pct_boundaries() -> None:
    assert severity_from_fill_pct(79, 80, 95) == 0
    assert severity_from_fill_pct(80, 80, 95) == 1
    assert severity_from_fill_pct(94, 80, 95) == 1
    assert severity_from_fill_pct(95, 80, 95) == 2
    assert severity_from_fill_pct(100, 80, 95) == 2


def test_fill_alert_escalation_no_cooldown() -> None:
    assert should_send_fill_alert(2, 0, 1, 1_000_000, 3600) is True
    assert should_send_fill_alert(1, 0, 0, 1_000_000, 3600) is True


def test_fill_alert_same_severity_respects_cooldown() -> None:
    assert should_send_fill_alert(1, 500, 1, 1000, 3600) is False
    assert should_send_fill_alert(1, 500, 1, 500 + 3600, 3600) is True


def test_fill_alert_zero_never_sends() -> None:
    assert should_send_fill_alert(0, 0, 2, 1_000_000, 3600) is False


def test_stats_cooldown() -> None:
    # ts_prev=0 is valid; bash uses (now - 0) >= cooldown (first send when now is large enough).
    assert should_send_stats_alert(0, 1_700_000_000, 600) is True
    assert should_send_stats_alert(100, 500, 600) is False
    assert should_send_stats_alert(100, 700, 600) is True


def test_u32_counter_delta_wrap() -> None:
    assert u32_counter_delta(2**32 - 10, 5) == 15
    assert u32_counter_delta(100, 200) == 100
    assert u32_counter_delta("", 1) is None


def test_evaluate_stats_cumulative_only() -> None:
    fire, reason = evaluate_stats_alert(
        has_conntrack=True,
        alert_on_stats=True,
        alert_on_stats_delta=False,
        interval_sec=None,
        dd=None,
        di=None,
        de=None,
        derr=None,
        dinv=None,
        dsr=None,
        drop_sum=5000,
        if_sum=0,
        ed_sum=0,
        er_sum=0,
        inv_sum=0,
        sr_sum=0,
        stats_drop_min=1000,
        stats_insert_failed_min=0,
        stats_delta_min_interval_sec=60,
        stats_delta_drop_min=0,
        stats_delta_insert_failed_min=0,
        stats_delta_early_drop_min=0,
        stats_delta_error_min=0,
        stats_delta_invalid_min=0,
        stats_delta_search_restart_min=0,
        stats_rate_drop_per_min=0,
        stats_rate_insert_failed_per_min=0,
        stats_rate_early_drop_per_min=0,
        stats_rate_error_per_min=0,
        stats_rate_invalid_per_min=0,
        stats_rate_search_restart_per_min=0,
    )
    assert fire is True
    assert "cumulative: drop=5000" in reason


def test_evaluate_stats_delta_blocked_short_interval() -> None:
    fire, reason = evaluate_stats_alert(
        has_conntrack=True,
        alert_on_stats=False,
        alert_on_stats_delta=True,
        interval_sec=30,
        dd=1000,
        di=0,
        de=0,
        derr=0,
        dinv=0,
        dsr=0,
        drop_sum=0,
        if_sum=0,
        ed_sum=0,
        er_sum=0,
        inv_sum=0,
        sr_sum=0,
        stats_drop_min=0,
        stats_insert_failed_min=0,
        stats_delta_min_interval_sec=60,
        stats_delta_drop_min=1,
        stats_delta_insert_failed_min=0,
        stats_delta_early_drop_min=0,
        stats_delta_error_min=0,
        stats_delta_invalid_min=0,
        stats_delta_search_restart_min=0,
        stats_rate_drop_per_min=0,
        stats_rate_insert_failed_per_min=0,
        stats_rate_early_drop_per_min=0,
        stats_rate_error_per_min=0,
        stats_rate_invalid_per_min=0,
        stats_rate_search_restart_per_min=0,
    )
    assert fire is False
    assert reason == ""


def test_metrics_phase_stats_send_telegram_respects_cooldown() -> None:
    out = metrics_phase_result(
        now_ts=2000,
        has_conntrack=True,
        p_ts=1000,
        p_drop=0,
        p_if=0,
        p_ed=0,
        p_er=0,
        p_inv=0,
        p_sr=0,
        drop_sum=5000,
        if_sum=0,
        ed_sum=0,
        er_sum=0,
        inv_sum=0,
        sr_sum=0,
        alert_on_stats=True,
        alert_on_stats_delta=False,
        stats_last_ts=1900,
        stats_cooldown_seconds=3600,
        stats_drop_min=1000,
        stats_insert_failed_min=0,
        stats_delta_min_interval_sec=60,
        stats_delta_drop_min=0,
        stats_delta_insert_failed_min=0,
        stats_delta_early_drop_min=0,
        stats_delta_error_min=0,
        stats_delta_invalid_min=0,
        stats_delta_search_restart_min=0,
        stats_rate_drop_per_min=0,
        stats_rate_insert_failed_per_min=0,
        stats_rate_early_drop_per_min=0,
        stats_rate_error_per_min=0,
        stats_rate_invalid_per_min=0,
        stats_rate_search_restart_per_min=0,
    )
    assert out["stats_fire"] is True
    assert out["stats_send_telegram"] is False


def test_cli_metrics_json_roundtrip() -> None:
    payload = {
        "phase": "metrics",
        "now_ts": 3000,
        "has_conntrack": True,
        "p_ts": 2000,
        "p_drop": 0,
        "p_if": 0,
        "p_ed": 0,
        "p_er": 0,
        "p_inv": 0,
        "p_sr": 0,
        "drop_sum": 100,
        "if_sum": 0,
        "ed_sum": 0,
        "er_sum": 0,
        "inv_sum": 0,
        "sr_sum": 0,
        "alert_on_stats": True,
        "alert_on_stats_delta": True,
        "stats_last_ts": 0,
        "stats_cooldown_seconds": 60,
        "stats_drop_min": 50,
        "stats_insert_failed_min": 0,
        "stats_delta_min_interval_sec": 60,
        "stats_delta_drop_min": 10,
        "stats_delta_insert_failed_min": 0,
        "stats_delta_early_drop_min": 0,
        "stats_delta_error_min": 0,
        "stats_delta_invalid_min": 0,
        "stats_delta_search_restart_min": 0,
        "stats_rate_drop_per_min": 0,
        "stats_rate_insert_failed_per_min": 0,
        "stats_rate_early_drop_per_min": 0,
        "stats_rate_error_per_min": 0,
        "stats_rate_invalid_per_min": 0,
        "stats_rate_search_restart_per_min": 0,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "cock_monitor", "conntrack-decide"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["interval_sec"] == 1000
    assert data["dd"] == 100
    assert data["stats_fire"] is True
