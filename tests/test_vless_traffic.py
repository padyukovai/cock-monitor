"""Unit tests for cock_monitor.domain.vless_traffic."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cock_monitor.domain import vless_traffic as vt


def test_fmt_bytes() -> None:
    assert vt.fmt_bytes(0) == "0 B"
    assert vt.fmt_bytes(1023) == "1023 B"
    assert "KB" in vt.fmt_bytes(2048)
    assert "MB" in vt.fmt_bytes(2 * 1024 * 1024)


def test_build_report_empty_prev_baseline() -> None:
    text, active, total, top1_e, top1_d = vt.build_report(
        host="h",
        title="t",
        subtitle="s",
        current_map={"a@x": 100},
        prev_map={},
        top_n=5,
        abuse_gb=100.0,
        abuse_share_pct=50.0,
        min_total_mb=1,
    )
    assert "Baseline recorded" in text
    assert active == 0 and total == 0
    assert top1_e == "" and top1_d == 0


def test_build_report_delta_and_abuse() -> None:
    # 10 GB delta -> abuse by absolute threshold if abuse_gb=5
    big = 10 * 1024 * 1024 * 1024
    text, active, total, top1_e, top1_d = vt.build_report(
        host="srv",
        title="daily",
        subtitle="sub",
        current_map={"u1@x": big + 100, "u2@x": 200},
        prev_map={"u1@x": 100, "u2@x": 100},
        top_n=10,
        abuse_gb=5.0,
        abuse_share_pct=40.0,
        min_total_mb=1,
    )
    assert active == 2
    assert total == big + 100  # u2 delta 100, u1 delta big
    assert top1_e == "u1@x"
    assert top1_d == big
    assert "Potential heavy downloaders" in text
    assert "u1@x" in text


def test_build_report_reset_negative_delta_clamped() -> None:
    text, active, total, _, _ = vt.build_report(
        host="h",
        title="t",
        subtitle="s",
        current_map={"a": 50, "b": 200},
        prev_map={"a": 1000, "b": 100},
        top_n=5,
        abuse_gb=1000.0,
        abuse_share_pct=99.0,
        min_total_mb=1,
    )
    assert "reset/anomaly" in text
    assert active == 1  # only b has positive delta
    assert total == 100


def test_parse_access_ts() -> None:
    dt = vt.parse_access_ts("2026/04/10 12:00:00.123")
    assert dt is not None
    assert dt.year == 2026


def test_extract_access_email_and_ip() -> None:
    line = (
        "2026/04/10 12:00:00 from tcp:1.2.3.4:12345 email:user@t\n"
    )
    assert vt.extract_access_email(line) == "user@t"
    assert vt.extract_ip_from_from_field(line) == "1.2.3.4"


def test_normalize_client_ip() -> None:
    assert vt.normalize_client_ip("192.0.2.1") == ("4", "192.0.2.1")
    v6 = vt.normalize_client_ip("2001:db8::1")
    assert v6 is not None
    assert v6[0] == "6"


def test_aggregate_vless_access_ips_daily_window(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    # MSK log lines; window is prev calendar day in MSK -> UTC
    log.write_text(
        "2026/04/09 22:00:00 from 198.51.100.1:443 email:a@test\n"
        "2026/04/09 23:30:00 from 198.51.100.2:443 email:a@test\n",
        encoding="utf-8",
    )
    tz = vt.load_tz("Europe/Moscow")
    w0, w1 = vt.daily_window_utc("2026-04-09", "2026-04-10", tz)
    agg, stats = vt.aggregate_vless_access_ips(
        [log],
        window_start_utc=w0,
        window_end_utc=w1,
        window_left_exclusive=False,
        log_tz=tz,
        allowed_emails={"a@test"},
        max_bytes_per_file=1024 * 1024,
        read_from_tail=False,
    )
    v4, v6 = agg["a@test"]
    assert len(v4) == 2
    assert len(v6) == 0
    assert stats.lines_matched == 2


def test_shrink_telegram_html_truncates() -> None:
    long = "x" * 5000
    out = vt.shrink_telegram_html(long, max_len=4000)
    assert len(out) <= 4000
