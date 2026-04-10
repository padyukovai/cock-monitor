from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from telegram_bot.status_provider import StatusProvider, truncate_for_telegram
from telegram_bot.telegram_client import TelegramClient


def _command_token(text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first = text.split(None, 1)[0]
    if "@" in first:
        first = first.split("@", 1)[0]
    return first.lower()


HELP_TEXT = (
    "cock-monitor bot commands:\n"
    "/status — full conntrack status\n"
    "/chart — PNG for last 24h from metrics DB (needs matplotlib)\n"
    "/vless_delta — VLESS usage delta since last sent report\n\n"
    "Alerts still come from the scheduled check."
)


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    status_provider: StatusProvider,
    chart_script: Path | None = None,
    env_file: Path | None = None,
    monitor_home: Path | None = None,
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
        client.send_message(str(chat_id), HELP_TEXT)
        return
    if cmd == "/chart":
        if chart_script is None or env_file is None:
            client.send_message(str(chat_id), "/chart is not configured (internal paths).")
            return
        if not chart_script.is_file():
            client.send_message(str(chat_id), "Chart script missing on server.")
            return
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            os.close(fd)
            out = Path(tmp_path)
            r = subprocess.run(
                [
                    sys.executable,
                    str(chart_script),
                    "--env-file",
                    str(env_file),
                    "--output",
                    str(out),
                ],
                cwd=str(chart_script.resolve().parent.parent),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "unknown error")[:1500]
                client.send_message(str(chat_id), f"chart failed:\n{err}")
                return
            client.send_photo(str(chat_id), out, caption="cock-monitor (on-demand chart)")
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
        report_script = (
            (monitor_home / "bin" / "cock-vless-daily-report.py")
            if monitor_home is not None
            else Path("/opt/cock-monitor/bin/cock-vless-daily-report.py")
        )
        if not report_script.is_file():
            report_script = Path("/opt/cock-monitor/bin/cock-vless-daily-report.py")
        if not report_script.is_file():
            client.send_message(str(chat_id), "VLESS report script missing on server.")
            return
        r = subprocess.run(
            [
                sys.executable,
                str(report_script),
                "--env-file",
                str(env_file),
                "--send-telegram",
                "--mode",
                "since-last-sent",
            ],
            cwd=str(report_script.resolve().parent.parent),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "unknown error")[:1500]
            client.send_message(str(chat_id), f"vless_delta failed:\n{err}")
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
