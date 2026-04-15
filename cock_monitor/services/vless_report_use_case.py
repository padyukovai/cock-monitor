"""VLESS report use-case: read inputs, build report, send, persist checkpoints."""
from __future__ import annotations

import os
import socket
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from telegram_bot.telegram_client import TelegramClient

from cock_monitor.adapters.vless_access_log import collect_access_log_ip_summary
from cock_monitor.adapters.vless_report_formatter import format_vless_report
from cock_monitor.adapters.xui_sqlite import fetch_client_traffics, fetch_vless_email_set
from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.domain.vless_traffic import load_tz
from cock_monitor.env import merge_env_into_process
from cock_monitor.storage.sqlite_connection import open_sqlite_connection
from cock_monitor.storage.vless_repository import (
    ensure_report_tables,
    get_checkpoint_map,
    get_last_sent_checkpoint_ts,
    get_snapshot_map,
    save_checkpoint,
    save_report_meta,
    transaction,
    upsert_snapshot,
)


class VlessReportError(Exception):
    """User-visible failure from VLESS report use-case."""


def run_vless_report_use_case(
    env_file: Path,
    *,
    mode: Literal["since-last-sent", "daily"],
    send_telegram: bool,
    dry_run: bool,
) -> None:
    env_path = env_file.expanduser().resolve()
    if not env_path.is_file():
        raise VlessReportError(f"env file not found: {env_path}")

    loaded = load_config(env_path)
    raw = loaded.app.raw
    merge_env_into_process(raw)

    xui_db_path = loaded.app.vless.xui_db_path
    if not xui_db_path:
        raise VlessReportError("XUI_DB_PATH is required")
    metrics_db = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    tz_name = loaded.app.vless.daily_tz
    top_n = loaded.app.vless.daily_top_n
    abuse_gb = loaded.app.vless.abuse_gb
    abuse_share_pct = loaded.app.vless.abuse_share_pct
    min_total_mb = loaded.app.vless.daily_min_total_mb
    ip_top_k = loaded.app.vless.ip_top_k
    ip_parse_max_mb = loaded.app.vless.ip_parse_max_mb
    ip_parse_max_bytes = ip_parse_max_mb * 1024 * 1024

    try:
        tz = load_tz(tz_name)
    except ValueError:
        raise VlessReportError(f"invalid VLESS_DAILY_TZ={tz_name!r}") from None

    telegram_tz_name = loaded.app.vless.telegram_display_tz
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

    try:
        xui_conn = open_sqlite_connection(xui_db_path, read_only=True, wal=False)
    except sqlite3.Error as e:
        raise VlessReportError(f"open x-ui.db failed: {e}") from e
    try:
        all_rows = fetch_client_traffics(xui_conn)
        vless_emails = fetch_vless_email_set(xui_conn)
    except sqlite3.Error as e:
        raise VlessReportError(f"query x-ui.db failed: {e}") from e
    finally:
        xui_conn.close()

    rows = [r for r in all_rows if r.email in vless_emails] if vless_emails else all_rows
    if not rows:
        raise VlessReportError("no client traffic rows found")

    try:
        met_conn = open_sqlite_connection(metrics_db)
    except sqlite3.Error as e:
        raise VlessReportError(f"open METRICS_DB failed: {e}") from e

    report_text = ""
    active_clients = 0
    total_delta = 0
    top1_email = ""
    top1_delta = 0
    sent_ok = False
    try:
        with transaction(met_conn):
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
                        datetime.fromtimestamp(last_sent_ts, tz=UTC)
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
                    summary = collect_access_log_ip_summary(
                        mode=mode,
                        log_path_raw=log_path_raw,
                        log_prev_raw=os.environ.get("VLESS_ACCESS_LOG_PATH_PREV", "").strip(),
                        log_tz_name=os.environ.get("VLESS_ACCESS_LOG_TZ", "").strip() or tz_name,
                        report_tz_name=tz_name,
                        prev_day_iso=prev_day,
                        snapshot_day_iso=snapshot_day,
                        last_sent_ts=last_sent_ts,
                        now_ts=ts,
                        allowed_emails=vless_emails if vless_emails else set(current_map.keys()),
                        max_bytes_per_file=ip_parse_max_bytes,
                    )
                    if summary.stats is None:
                        print(
                            "cock-vless-daily-report: "
                            f"VLESS_ACCESS_LOG_PATH not a readable file: {log_path_raw!r}",
                            file=sys.stderr,
                        )
                    else:
                        ip_counts = summary.counts
                        ip_truncated = summary.truncated
                        print(
                            "cock-vless-daily-report: "
                            f"ip_parse_ms={summary.stats.elapsed_ms} "
                            f"ip_bytes_read={summary.stats.bytes_read} "
                            f"ip_lines_matched={summary.stats.lines_matched} "
                            f"ip_truncated={1 if summary.stats.truncated else 0}",
                            file=sys.stderr,
                        )

            report_text, active_clients, total_delta, top1_email, top1_delta = format_vless_report(
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
                token = loaded.app.telegram.bot_token
                chat = loaded.app.telegram.chat_id
                if not token or not chat:
                    raise VlessReportError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required")
                TelegramClient(token).send_message(chat, report_text, parse_mode="HTML")
                sent_ok = True
                if mode == "since-last-sent":
                    save_checkpoint(met_conn, ts=ts, rows=rows, source="since_last_sent")

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
    except sqlite3.Error as e:
        raise VlessReportError(f"sqlite failure: {e}") from e
    except RuntimeError as e:
        raise VlessReportError(str(e)) from e
    finally:
        met_conn.close()
