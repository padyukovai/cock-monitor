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
from mtproxy_module.reports import build_period_caption, current_status_text
from mtproxy_module.repository import connect_db, init_schema, summary_rows, update_threshold

from telegram_bot.runtime import run_with_timeout
from telegram_bot.status_provider import StatusProvider, truncate_for_telegram
from telegram_bot.telegram_client import TelegramClient

_BOT_CMD_TIMEOUT_SEC = 120.0

BASE_BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Full conntrack status"),
    ("chart", "PNG chart for last 24h"),
    ("vless_delta", "VLESS usage delta report"),
    ("help", "Show command help"),
)

MTPROXY_BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("mt_status", "MTProxy live status snapshot"),
    ("mt_today", "MTProxy report + chart for last 24h"),
    ("mt_threshold", "Update MTProxy thresholds"),
)


def _send_cmd_failure(client: TelegramClient, chat_id: str, cmd: str, message: str) -> None:
    client.send_message(chat_id, f"{cmd} failed:\n{message}"[:2000])


def _run_command_with_timeout(
    client: TelegramClient,
    chat_id: str,
    cmd: str,
    fn: Any,
    *,
    timeout_sec: float = _BOT_CMD_TIMEOUT_SEC,
    known_exceptions: tuple[type[BaseException], ...] = (),
) -> tuple[bool, Any]:
    try:
        return True, run_with_timeout(fn, timeout_sec)
    except FutureTimeout:
        _send_cmd_failure(client, chat_id, cmd, f"timed out after {timeout_sec:.0f}s")
    except known_exceptions as e:
        _send_cmd_failure(client, chat_id, cmd, str(e))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as e:
        _send_cmd_failure(client, chat_id, cmd, str(e))
    return False, None


def _command_token(text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first = text.split(None, 1)[0]
    if "@" in first:
        first = first.split("@", 1)[0]
    return first.lower()


def bot_commands(*, mtproxy_enabled: bool) -> list[tuple[str, str]]:
    commands = list(BASE_BOT_COMMANDS)
    if mtproxy_enabled:
        commands.extend(MTPROXY_BOT_COMMANDS)
    return commands


def _help_text(mtproxy_enabled: bool) -> str:
    lines = ["cock-monitor bot commands:"]
    for name, desc in BASE_BOT_COMMANDS:
        if name == "chart":
            lines.append(f"/{name} — {desc} (needs matplotlib)")
            continue
        lines.append(f"/{name} — {desc}")
    lines.append("")
    lines.append("Alerts still come from the scheduled check.")
    if mtproxy_enabled:
        lines.append("")
        lines.append("MTProxy module commands:")
        lines.append("/mt_status — MTProxy live status snapshot")
        lines.append("/mt_today — MTProxy report + chart for last 24h")
        lines.append("/mt_threshold <warning|critical> <value> — update MTProxy thresholds")
    return "\n".join(lines)


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    status_provider: StatusProvider,
    env_file: Path | None = None,
    mtproxy_cfg: MtproxyConfig | None = None,
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
        if not mtproxy_cfg or not mtproxy_cfg.enabled:
            client.send_message(str(chat_id), "MTProxy module is disabled.")
            return

        def _run_mtproxy_query(
            cmd_name: str,
            query: Any,
            *,
            known_exceptions: tuple[type[BaseException], ...] = (),
        ) -> tuple[bool, Any]:
            def _wrapped() -> Any:
                conn = connect_db(mtproxy_cfg.db_path)
                try:
                    init_schema(conn)
                    return query(conn)
                finally:
                    conn.close()

            return _run_command_with_timeout(
                client,
                str(chat_id),
                cmd_name,
                _wrapped,
                known_exceptions=known_exceptions,
            )

        if cmd == "/mt_status":
            ok, body = _run_mtproxy_query(
                "mt_status",
                lambda conn: current_status_text(conn, mtproxy_cfg),
            )
            if not ok:
                return
            client.send_message(str(chat_id), truncate_for_telegram(body))
            return
        if cmd == "/mt_today":
            start_ts = int(time.time()) - 24 * 3600
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            try:
                os.close(fd)
                out = Path(tmp_path)
                ok, payload = _run_mtproxy_query(
                    "mt_today",
                    lambda conn: (
                        summary_rows(conn, start_ts),
                        build_period_caption(
                            conn,
                            start_ts,
                            title="MTProxy - Report (24h)",
                            top_n=mtproxy_cfg.daily_report_top_n,
                        ),
                    ),
                )
                if not ok:
                    return
                rows, cap = payload
                ok, _ = _run_command_with_timeout(
                    client,
                    str(chat_id),
                    "mt_today",
                    lambda: generate_mtproxy_chart(
                        rows,
                        out,
                        title=f"MTProxy Load - {time.strftime('%d.%m.%Y')}",
                    ),
                    known_exceptions=(ImportError,),
                )
                if not ok:
                    return
                client.send_photo(str(chat_id), out, caption=cap)
            except ImportError:
                client.send_message(str(chat_id), "matplotlib is required for /mt_today.")
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
            ok, msg = _run_mtproxy_query(
                "mt_threshold",
                lambda conn: update_threshold(conn, param, value),
            )
            if not ok:
                return
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

            ok, caption = _run_command_with_timeout(
                client,
                str(chat_id),
                "chart",
                _chart,
                known_exceptions=(FileNotFoundError, RuntimeError),
            )
            if not ok:
                return
            if not isinstance(caption, str):
                _send_cmd_failure(client, str(chat_id), "chart", "empty chart caption")
                return
            try:
                client.send_photo(str(chat_id), out, caption=caption)
            except ImportError as e:
                client.send_message(
                    str(chat_id),
                    "chart failed:\nmatplotlib required (e.g. apt install python3-matplotlib)\n"
                    + str(e)[:800],
                )
            except (OSError, RuntimeError) as e:
                _send_cmd_failure(client, str(chat_id), "chart", str(e))
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

        _run_command_with_timeout(
            client,
            str(chat_id),
            "vless_delta",
            _vless,
            known_exceptions=(VlessReportError,),
        )
        return

    if cmd != "/status":
        return
    ok, body = status_provider.get_status()
    if not ok:
        _send_cmd_failure(client, str(chat_id), "status", body)
        return
    client.send_message(str(chat_id), truncate_for_telegram(body))
