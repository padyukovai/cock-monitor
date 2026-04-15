from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from cock_monitor.services.daily_chart import run_daily_chart
from cock_monitor.services.vless_report import VlessReportError, run_since_last_sent_with_telegram
from mtproxy_module.charts import generate_mtproxy_chart
from mtproxy_module.config import MtproxyConfig
from mtproxy_module.repository import init_schema, summary_rows, update_threshold
from mtproxy_module.reports import build_period_caption, current_status_text
from telegram_bot.runtime import run_with_timeout
from telegram_bot.status_provider import StatusProvider, truncate_for_telegram
from telegram_bot.telegram_client import TelegramClient

_BOT_CMD_TIMEOUT_SEC = 120.0


def _command_token(text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first = text.split(None, 1)[0]
    if "@" in first:
        first = first.split("@", 1)[0]
    return first.lower()


BASE_HELP_TEXT = (
    "cock-monitor bot commands:\n"
    "/status — full conntrack status\n"
    "/chart — PNG for last 24h from metrics DB (needs matplotlib)\n"
    "/vless_delta — VLESS usage delta since last sent report\n\n"
    "Alerts still come from the scheduled check."
)


def _help_text(mtproxy_enabled: bool) -> str:
    if not mtproxy_enabled:
        return BASE_HELP_TEXT
    return (
        BASE_HELP_TEXT
        + "\n\nMTProxy module commands:\n"
        "/mt_status — MTProxy live status snapshot\n"
        "/mt_today — MTProxy report + chart for last 24h\n"
        "/mt_threshold <warning|critical> <value> — update MTProxy thresholds"
    )


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    status_provider: StatusProvider,
    env_file: Path | None = None,
    mtproxy_cfg: MtproxyConfig | None = None,
    mtproxy_conn: sqlite3.Connection | None = None,
) -> None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    if str(chat_id) != str(allowed_chat_id):
        return
    text = msg.get("text")
    if not isinstance(text, str):
        return
    cmd = _command_token(text)
    if cmd is None:
        return
    if cmd in ("/start", "/help"):
        client.send_message(str(chat_id), _help_text(mtproxy_enabled=bool(mtproxy_cfg and mtproxy_cfg.enabled)))
        return

    if cmd.startswith("/mt_"):
        if not mtproxy_cfg or not mtproxy_cfg.enabled or mtproxy_conn is None:
            client.send_message(str(chat_id), "MTProxy module is disabled.")
            return
        init_schema(mtproxy_conn)
        if cmd == "/mt_status":
            body = current_status_text(mtproxy_conn, mtproxy_cfg)
            client.send_message(str(chat_id), truncate_for_telegram(body))
            return
        if cmd == "/mt_today":
            start_ts = int(time.time()) - 24 * 3600
            rows = summary_rows(mtproxy_conn, start_ts)
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            try:
                os.close(fd)
                out = Path(tmp_path)
                generate_mtproxy_chart(
                    rows,
                    out,
                    title=f"MTProxy Load - {time.strftime('%d.%m.%Y')}",
                )
                cap = build_period_caption(
                    mtproxy_conn,
                    start_ts,
                    title="MTProxy - Report (24h)",
                    top_n=mtproxy_cfg.daily_report_top_n,
                )
                client.send_photo(str(chat_id), out, caption=cap)
            except ImportError:
                client.send_message(str(chat_id), "matplotlib is required for /mt_today.")
            except (OSError, RuntimeError) as e:
                client.send_message(str(chat_id), f"/mt_today failed: {e}"[:2000])
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return
        if cmd == "/mt_threshold":
            parts = text.split()
            if len(parts) != 3:
                client.send_message(str(chat_id), "Usage: /mt_threshold <warning|critical> <value>")
                return
            param = parts[1].strip().lower()
            try:
                value = int(parts[2])
            except ValueError:
                client.send_message(str(chat_id), "Invalid value. Must be integer.")
                return
            msg = update_threshold(mtproxy_conn, param, value)
            client.send_message(str(chat_id), msg[:2000])
            return
        return

    if cmd == "/chart":
        if env_file is None:
            client.send_message(str(chat_id), "/chart is not configured (env file missing).")
            return
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            os.close(fd)
            out = Path(tmp_path)

            def _chart() -> str:
                return run_daily_chart(env_file, out)

            try:
                caption = run_with_timeout(_chart, _BOT_CMD_TIMEOUT_SEC)
            except FutureTimeout:
                client.send_message(
                    str(chat_id),
                    f"chart failed:\ntimed out after {_BOT_CMD_TIMEOUT_SEC:.0f}s",
                )
                return
            except FileNotFoundError as e:
                client.send_message(str(chat_id), f"chart failed:\n{e}"[:1500])
                return
            except ImportError as e:
                client.send_message(
                    str(chat_id),
                    "chart failed:\nmatplotlib required (e.g. apt install python3-matplotlib)\n"
                    + str(e)[:800],
                )
                return
            except RuntimeError as e:
                client.send_message(str(chat_id), f"chart failed:\n{e}"[:1500])
                return

            client.send_photo(str(chat_id), out, caption=caption)
        except (OSError, RuntimeError) as e:
            client.send_message(str(chat_id), f"chart error: {e}"[:2000])
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
        return

    if cmd == "/vless_delta":
        if env_file is None:
            client.send_message(str(chat_id), "/vless_delta is not configured (env file missing).")
            return

        def _vless() -> None:
            run_since_last_sent_with_telegram(env_file)

        try:
            run_with_timeout(_vless, _BOT_CMD_TIMEOUT_SEC)
        except FutureTimeout:
            client.send_message(
                str(chat_id),
                f"vless_delta failed:\ntimed out after {_BOT_CMD_TIMEOUT_SEC:.0f}s",
            )
        except VlessReportError as e:
            client.send_message(str(chat_id), f"vless_delta failed:\n{e}"[:1500])
        return

    if cmd != "/status":
        return
    ok, body = status_provider.get_status()
    if not ok:
        client.send_message(
            str(chat_id),
            "Status failed:\n" + body[:2000],
        )
        return
    client.send_message(str(chat_id), truncate_for_telegram(body))
