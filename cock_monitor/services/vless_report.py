#!/usr/bin/env python3
"""Build and send VLESS traffic reports from 3x-ui sqlite counters."""
from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from cock_monitor.adapters.xui_sqlite import TrafficRow, fetch_client_traffics, fetch_vless_email_set
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.domain.vless_traffic import (
    IpParseStats,
    aggregate_vless_access_ips,
    build_report,
    daily_window_utc,
    load_tz,
)
from cock_monitor.env import merge_env_into_process, parse_env_file
from cock_monitor.storage.vless_repository import (
    ensure_report_tables,
    get_checkpoint_map,
    get_last_sent_checkpoint_ts,
    get_snapshot_map,
    save_checkpoint,
    save_report_meta,
    upsert_snapshot,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class VlessReportError(Exception):
    """User-visible failure from run_vless_report (message only, no stack trace in Telegram)."""


def _repo_root() -> Path:
    return _REPO_ROOT


def _load_telegram_client():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from telegram_bot.telegram_client import TelegramClient

    return TelegramClient


def _get_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def run_vless_report(
    env_file: Path,
    *,
    mode: Literal["since-last-sent", "daily"],
    send_telegram: bool,
    dry_run: bool,
) -> None:
    env_path = env_file.expanduser().resolve()
    if not env_path.is_file():
        raise VlessReportError(f"env file not found: {env_path}")

    raw = parse_env_file(env_path)
    merge_env_into_process(raw)

    xui_db_path = os.environ.get("XUI_DB_PATH", "").strip()
    if not xui_db_path:
        raise VlessReportError("XUI_DB_PATH is required")
    metrics_db = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    tz_name = os.environ.get("VLESS_DAILY_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    top_n = max(1, _get_int_env("VLESS_DAILY_TOP_N", 10))
    abuse_gb = max(0.0, _get_float_env("VLESS_ABUSE_GB", 20.0))
    abuse_share_pct = max(0.0, _get_float_env("VLESS_ABUSE_SHARE_PCT", 40.0))
    min_total_mb = max(0, _get_int_env("VLESS_DAILY_MIN_TOTAL_MB", 500))
    ip_top_k = max(1, _get_int_env("VLESS_IP_TOP_K", 3))
    ip_parse_max_mb = max(1, _get_int_env("VLESS_IP_PARSE_MAX_MB", 256))
    ip_parse_max_bytes = ip_parse_max_mb * 1024 * 1024

    try:
        tz = load_tz(tz_name)
    except ValueError:
        raise VlessReportError(f"invalid VLESS_DAILY_TZ={tz_name!r}") from None

    telegram_tz_name = (
        os.environ.get("VLESS_TELEGRAM_DISPLAY_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    )
    try:
        telegram_display_tz = load_tz(telegram_tz_name)
    except ValueError:
        telegram_display_tz = load_tz("Europe/Moscow")
        telegram_tz_name = "Europe/Moscow"

    now = datetime.now(tz)
    snapshot_day = now.date().isoformat()
    prev_day = (now.date() - timedelta(days=1)).isoformat()
    usage_day = prev_day
    ts = int(time.time())

    xui_uri = f"file:{xui_db_path}?mode=ro"
    try:
        xui_conn = sqlite3.connect(xui_uri, uri=True)
    except sqlite3.Error as e:
        raise VlessReportError(f"open x-ui.db failed: {e}") from e

    try:
        all_rows = fetch_client_traffics(xui_conn)
        vless_emails = fetch_vless_email_set(xui_conn)
    except sqlite3.Error as e:
        raise VlessReportError(f"query x-ui.db failed: {e}") from e
    finally:
        xui_conn.close()

    if vless_emails:
        rows = [r for r in all_rows if r.email in vless_emails]
    else:
        # Fallback to all rows if protocol mapping is unavailable in this version/schema.
        rows = all_rows

    if not rows:
        raise VlessReportError("no client traffic rows found")

    try:
        met_conn = sqlite3.connect(metrics_db)
    except sqlite3.Error as e:
        raise VlessReportError(f"open METRICS_DB failed: {e}") from e

    report_text = ""
    active_clients = 0
    total_delta = 0
    top1_email = ""
    top1_delta = 0
    sent_ok = False
    try:
        ensure_report_tables(met_conn)
        upsert_snapshot(met_conn, snapshot_day_msk=snapshot_day, ts=ts, rows=rows)
        current_map = {r.email: r.total for r in rows}
        last_sent_ts: int | None = None
        if mode == "daily":
            prev_map = get_snapshot_map(met_conn, prev_day)
            report_title = f"VLESS daily usage ({tz_name}): {usage_day}"
            report_subtitle = "Delta period: previous day snapshot -> current snapshot"
        else:
            last_sent_ts = get_last_sent_checkpoint_ts(met_conn, source="since_last_sent")
            prev_map = get_checkpoint_map(met_conn, last_sent_ts) if last_sent_ts else {}
            if last_sent_ts:
                last_dt = (
                    datetime.fromtimestamp(last_sent_ts, tz=timezone.utc)
                    .astimezone(telegram_display_tz)
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
                report_subtitle = (
                    f"Delta period: since last sent report at {last_dt} ({telegram_tz_name})"
                )
            else:
                report_subtitle = "Delta period: since last sent report (no baseline yet)"
            report_title = f"VLESS delta since last report ({tz_name})"

        ip_counts: dict[str, tuple[int, int]] | None = None
        ip_truncated = False
        if prev_map:
            log_path_raw = os.environ.get("VLESS_ACCESS_LOG_PATH", "").strip()
            if log_path_raw:
                log_path = Path(log_path_raw)
                log_paths: list[Path] = []
                if log_path.is_file():
                    log_paths.append(log_path)
                prev_log = os.environ.get("VLESS_ACCESS_LOG_PATH_PREV", "").strip()
                if prev_log:
                    pp = Path(prev_log)
                    if pp.is_file():
                        log_paths.append(pp)
                else:
                    alt = Path(str(log_path) + ".1")
                    if alt.is_file():
                        log_paths.append(alt)
                if not log_paths:
                    print(
                        f"cock-vless-daily-report: VLESS_ACCESS_LOG_PATH not a readable file: {log_path_raw!r}",
                        file=sys.stderr,
                    )
                else:
                    log_tz_name = os.environ.get("VLESS_ACCESS_LOG_TZ", "").strip() or tz_name
                    try:
                        log_tz = load_tz(log_tz_name)
                    except ValueError:
                        print(
                            f"cock-vless-daily-report: invalid VLESS_ACCESS_LOG_TZ={log_tz_name!r}",
                            file=sys.stderr,
                        )
                        log_tz = tz
                    allowed_emails = vless_emails if vless_emails else set(current_map.keys())
                    if mode == "daily":
                        w0, w1 = daily_window_utc(prev_day, snapshot_day, tz)
                        agg, ip_stats = aggregate_vless_access_ips(
                            log_paths,
                            window_start_utc=w0,
                            window_end_utc=w1,
                            window_left_exclusive=False,
                            log_tz=log_tz,
                            allowed_emails=allowed_emails,
                            max_bytes_per_file=ip_parse_max_bytes,
                            read_from_tail=False,
                        )
                    else:
                        if last_sent_ts is not None:
                            end_ts = time.time()
                            w0 = datetime.fromtimestamp(last_sent_ts, tz=timezone.utc)
                            w1 = datetime.fromtimestamp(end_ts, tz=timezone.utc)
                            agg, ip_stats = aggregate_vless_access_ips(
                                log_paths,
                                window_start_utc=w0,
                                window_end_utc=w1,
                                window_left_exclusive=True,
                                log_tz=log_tz,
                                allowed_emails=allowed_emails,
                                max_bytes_per_file=ip_parse_max_bytes,
                                read_from_tail=True,
                            )
                        else:
                            agg, ip_stats = {}, IpParseStats(0, 0, 0, False)
                    ip_counts = (
                        {e: (len(v4), len(v6)) for e, (v4, v6) in agg.items()} if agg else None
                    )
                    ip_truncated = ip_stats.truncated
                    print(
                        "cock-vless-daily-report: "
                        f"ip_parse_ms={ip_stats.elapsed_ms} "
                        f"ip_bytes_read={ip_stats.bytes_read} "
                        f"ip_lines_matched={ip_stats.lines_matched} "
                        f"ip_truncated={1 if ip_stats.truncated else 0}",
                        file=sys.stderr,
                    )

        report_text, active_clients, total_delta, top1_email, top1_delta = build_report(
            host=socket.gethostname(),
            title=report_title,
            subtitle=report_subtitle,
            current_map=current_map,
            prev_map=prev_map,
            top_n=top_n,
            abuse_gb=abuse_gb,
            abuse_share_pct=abuse_share_pct,
            min_total_mb=min_total_mb,
            ip_counts=ip_counts,
            ip_top_k=ip_top_k,
            ip_truncated=ip_truncated,
        )

        if dry_run or not send_telegram:
            print(report_text)
            sent_ok = True
        else:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if not token or not chat:
                raise VlessReportError(
                    "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required"
                )
            TelegramClient = _load_telegram_client()
            client = TelegramClient(token)
            client.send_message(chat, report_text, parse_mode="HTML")
            sent_ok = True
            if mode == "since-last-sent":
                save_checkpoint(met_conn, ts=ts, rows=rows, source="since_last_sent")
    except sqlite3.Error as e:
        raise VlessReportError(f"sqlite failure: {e}") from e
    except RuntimeError as e:
        raise VlessReportError(str(e)) from e
    finally:
        try:
            if report_text:
                save_report_meta(
                    met_conn,
                    snapshot_day_msk=snapshot_day,
                    ts=ts,
                    total_clients=active_clients,
                    total_delta_bytes=total_delta,
                    top1_email=top1_email,
                    top1_delta_bytes=top1_delta,
                    sent_ok=sent_ok,
                )
        finally:
            met_conn.close()


def run_since_last_sent_with_telegram(env_file: Path) -> None:
    """On-demand /vless_delta: same as CLI --send-telegram --mode since-last-sent."""
    run_vless_report(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor VLESS daily usage report")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/cock-monitor.env"),
        help="Env file with XUI_DB_PATH, METRICS_DB and optional Telegram vars",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send report to Telegram (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)",
    )
    parser.add_argument(
        "--mode",
        choices=("since-last-sent", "daily"),
        default="since-last-sent",
        help="Report mode: since-last-sent (default) or daily (D vs D-1 in report TZ)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without Telegram send",
    )
    args = parser.parse_args()
    try:
        run_vless_report(
            args.env_file,
            mode=args.mode,
            send_telegram=args.send_telegram,
            dry_run=args.dry_run,
        )
    except VlessReportError as e:
        print(f"cock-vless-daily-report: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
