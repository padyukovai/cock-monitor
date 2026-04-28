"""Adapter for extracting VLESS unique-IP counts from Xray access logs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cock_monitor.domain.vless_traffic import (
    IpParseStats,
    aggregate_vless_access_ips,
    daily_window_utc,
    load_tz,
)

UTC = UTC


@dataclass(frozen=True)
class AccessLogIpSummary:
    counts: dict[str, tuple[int, int]] | None
    truncated: bool
    stats: IpParseStats | None


def collect_access_log_ip_summary(
    *,
    mode: Literal["since-last-sent", "daily"],
    log_path_raw: str,
    log_prev_raw: str,
    log_tz_name: str,
    report_tz_name: str,
    prev_day_iso: str,
    snapshot_day_iso: str,
    last_sent_ts: int | None,
    now_ts: int,
    allowed_emails: set[str],
    max_bytes_per_file: int,
) -> AccessLogIpSummary:
    """Collect unique IPv4/IPv6 counts per VLESS email for report window."""
    log_path = Path(log_path_raw)
    log_paths: list[Path] = []
    if log_path.is_file():
        log_paths.append(log_path)
    if log_prev_raw:
        explicit_prev = Path(log_prev_raw)
        if explicit_prev.is_file():
            log_paths.append(explicit_prev)
    else:
        rotated = Path(str(log_path) + ".1")
        if rotated.is_file():
            log_paths.append(rotated)
    if not log_paths:
        return AccessLogIpSummary(counts=None, truncated=False, stats=None)

    try:
        log_tz = load_tz(log_tz_name)
    except ValueError:
        log_tz = load_tz(report_tz_name)

    if mode == "daily":
        report_tz = load_tz(report_tz_name)
        w0, w1 = daily_window_utc(prev_day_iso, snapshot_day_iso, report_tz)
        agg, ip_stats = aggregate_vless_access_ips(
            log_paths,
            window_start_utc=w0,
            window_end_utc=w1,
            window_left_exclusive=False,
            log_tz=log_tz,
            allowed_emails=allowed_emails,
            max_bytes_per_file=max_bytes_per_file,
            read_from_tail=False,
        )
    elif last_sent_ts is not None:
        w0 = datetime.fromtimestamp(last_sent_ts, tz=UTC)
        w1 = datetime.fromtimestamp(now_ts, tz=UTC)
        agg, ip_stats = aggregate_vless_access_ips(
            log_paths,
            window_start_utc=w0,
            window_end_utc=w1,
            window_left_exclusive=True,
            log_tz=log_tz,
            allowed_emails=allowed_emails,
            max_bytes_per_file=max_bytes_per_file,
            read_from_tail=True,
        )
    else:
        return AccessLogIpSummary(counts={}, truncated=False, stats=IpParseStats(0, 0, 0, False))

    counts = {email: (len(v4), len(v6)) for email, (v4, v6) in agg.items()} if agg else None
    return AccessLogIpSummary(counts=counts, truncated=ip_stats.truncated, stats=ip_stats)
