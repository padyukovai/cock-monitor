"""MTProxy module Telegram handlers."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cock_monitor.modules.mtproxy.charts import generate_mtproxy_chart
from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.modules.mtproxy.reports import build_period_caption, current_status_text
from cock_monitor.modules.mtproxy.repository import connect_db, init_schema, summary_rows, update_threshold
from cock_monitor.platform.telegram.handler_utils import (
    TelegramHandlerContext,
    run_command_with_timeout,
    truncate_for_telegram,
)


def _run_mtproxy_query(
    ctx: TelegramHandlerContext,
    mt_cfg: MtproxyConfig,
    cmd_name: str,
    query: Callable[[sqlite3.Connection], Any],
) -> tuple[bool, Any]:
    def _wrapped() -> Any:
        conn = connect_db(mt_cfg.db_path)
        try:
            init_schema(conn)
            return query(conn)
        finally:
            conn.close()

    return run_command_with_timeout(ctx.client, ctx.chat_id, cmd_name, _wrapped)


def handle_mt_status(ctx: TelegramHandlerContext) -> None:
    mt_cfg = MtproxyConfig.from_env_map(ctx.raw_env)
    ok, body = _run_mtproxy_query(
        ctx,
        mt_cfg,
        "mt_status",
        lambda conn: current_status_text(conn, mt_cfg),
    )
    if ok and isinstance(body, str):
        ctx.client.send_message(ctx.chat_id, truncate_for_telegram(body))


def handle_mt_today(ctx: TelegramHandlerContext) -> None:
    mt_cfg = MtproxyConfig.from_env_map(ctx.raw_env)
    start_ts = int(time.time()) - 24 * 3600
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    try:
        import os

        os.close(fd)
        out = Path(tmp_path)
        ok, payload = _run_mtproxy_query(
            ctx,
            mt_cfg,
            "mt_today",
            lambda conn: (
                summary_rows(conn, start_ts),
                build_period_caption(
                    conn,
                    start_ts,
                    title="MTProxy - Report (24h)",
                    top_n=mt_cfg.daily_report_top_n,
                ),
            ),
        )
        if not ok or not isinstance(payload, tuple):
            return
        rows, cap = payload
        ok2, _ = run_command_with_timeout(
            ctx.client,
            ctx.chat_id,
            "mt_today",
            lambda: generate_mtproxy_chart(rows, out, title=f"MTProxy Load - {time.strftime('%d.%m.%Y')}"),
            known_exceptions=(ImportError,),
        )
        if ok2:
            ctx.client.send_photo(ctx.chat_id, out, caption=cap)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def handle_mt_threshold(ctx: TelegramHandlerContext) -> None:
    mt_cfg = MtproxyConfig.from_env_map(ctx.raw_env)
    parts = ctx.text.split()
    if len(parts) != 3:
        ctx.client.send_message(ctx.chat_id, "Usage: /mt_threshold <warning|critical> <value>")
        return
    param = parts[1].strip().lower()
    try:
        value = int(parts[2])
    except ValueError:
        ctx.client.send_message(ctx.chat_id, "Invalid value. Must be integer.")
        return
    ok, msg = _run_mtproxy_query(
        ctx,
        mt_cfg,
        "mt_threshold",
        lambda conn: update_threshold(conn, param, value),
    )
    if ok and isinstance(msg, str):
        ctx.client.send_message(ctx.chat_id, msg[:2000])
