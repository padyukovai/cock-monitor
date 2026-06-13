"""Unified Telegram command dispatch."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from cock_monitor.config_loader import load_config
from cock_monitor.modules.core.charts import run_core_chart
from cock_monitor.modules.core.status import build_core_status
from cock_monitor.modules.mtproxy.charts import generate_mtproxy_chart
from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.modules.mtproxy.reports import build_period_caption, current_status_text
from cock_monitor.modules.mtproxy.repository import connect_db, init_schema, summary_rows, update_threshold
from cock_monitor.modules.wg.service import wg_status_text
from cock_monitor.platform.registry import get_registry, module_enabled, parse_enabled_modules
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.runtime import run_with_timeout
from cock_monitor.services.vless_report import (
    VlessReportError,
    run_daily_with_telegram,
    run_since_last_sent_with_telegram,
)

_BOT_CMD_TIMEOUT_SEC = 120.0
_VLESS_DELTA_SINCE_LAST_FLAGS = {"--since-last-sent", "--since-last"}
_CAPTION_MAX = 4096


def truncate_for_telegram(text: str, limit: int = _CAPTION_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _send_cmd_failure(client: TelegramClient, chat_id: str, cmd: str, message: str) -> None:
    client.send_message(chat_id, f"{cmd} failed:\n{message}"[:2000])


def _run_command_with_timeout(
    client: TelegramClient,
    chat_id: str,
    cmd: str,
    fn: Callable[[], Any],
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


def build_help_text(env: dict[str, str]) -> str:
    registry = get_registry()
    lines = ["cock-monitor v2 — enabled modules:", ", ".join(parse_enabled_modules(env)), ""]
    for cmd in registry.telegram_commands(env):
        lines.append(f"/{cmd.name} — {cmd.help_text}")
    lines.append("")
    lines.append("Scheduled alerts come from enabled module timers.")
    return "\n".join(lines)


def _parse_env_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("'\"")
    return out


def _upsert_env_key(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    replaced = False
    for idx, raw in enumerate(lines):
        stripped = raw.lstrip()
        if stripped.startswith("#") or "=" not in raw:
            continue
        line = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        cur_key, _, _ = line.partition("=")
        if cur_key.strip() != key:
            continue
        prefix = raw[: len(raw) - len(stripped)]
        export_part = "export " if stripped.startswith("export ") else ""
        lines[idx] = f"{prefix}{export_part}{key}={value}"
        replaced = True
        break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_global_cake_limit(*, iface: str, rate_mbit: int) -> str:
    cmd = [
        "tc", "qdisc", "replace", "dev", iface, "root", "cake",
        "bandwidth", f"{rate_mbit}mbit", "flowblind", "dual-dsthost",
    ]
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if out.returncode != 0:
        err = (out.stderr or out.stdout or "unknown tc error").strip()
        raise RuntimeError(f"tc apply failed on {iface}: {err}")
    return f"Applied global CAKE limit on {iface}: {rate_mbit}M"


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    env_file: Path,
) -> None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return
    chat_id = str(chat.get("id"))
    if chat_id != str(allowed_chat_id):
        return
    text = msg.get("text")
    if not isinstance(text, str):
        return
    cmd = _command_token(text)
    if cmd is None:
        return

    raw_env = load_config(env_file).app.raw

    if cmd in ("/start", "/help"):
        client.send_message(chat_id, build_help_text(raw_env))
        return

    if cmd == "/status" and module_enabled("core", raw_env):
        ok, body = _run_command_with_timeout(
            client,
            chat_id,
            "status",
            lambda: build_core_status(env_file),
        )
        if ok and isinstance(body, str):
            extra = ""
            if module_enabled("wg", raw_env):
                try:
                    extra = "\n\n--- WireGuard ---\n" + wg_status_text(env_file)
                except OSError:
                    pass
            client.send_message(chat_id, truncate_for_telegram(body + extra))
        return

    if cmd == "/chart" and module_enabled("core", raw_env):
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            os.close(fd)
            out = Path(tmp_path)
            ok, caption = _run_command_with_timeout(
                client,
                chat_id,
                "chart",
                lambda: run_core_chart(env_file, out),
                known_exceptions=(FileNotFoundError, RuntimeError, ImportError),
            )
            if ok and isinstance(caption, str):
                client.send_photo(chat_id, out, caption=caption)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return

    if cmd == "/wg_status" and module_enabled("wg", raw_env):
        ok, body = _run_command_with_timeout(client, chat_id, "wg_status", lambda: wg_status_text(env_file))
        if ok and isinstance(body, str):
            client.send_message(chat_id, truncate_for_telegram(body))
        return

    if cmd.startswith("/mt_") and module_enabled("mtproxy", raw_env):
        mt_cfg = MtproxyConfig.from_env_map(raw_env)
        _handle_mtproxy(client, chat_id, cmd, text, mt_cfg)
        return

    if cmd == "/vless_delta" and module_enabled("vless", raw_env):
        _handle_vless(client, chat_id, text, env_file)
        return

    if cmd == "/cake_bw" and module_enabled("shaper", raw_env):
        _handle_cake_bw(client, chat_id, text, env_file)
        return


def _handle_mtproxy(client: TelegramClient, chat_id: str, cmd: str, text: str, mt_cfg: MtproxyConfig) -> None:
    def _run_mtproxy_query(cmd_name: str, query: Callable[[sqlite3.Connection], Any]) -> tuple[bool, Any]:
        def _wrapped() -> Any:
            conn = connect_db(mt_cfg.db_path)
            try:
                init_schema(conn)
                return query(conn)
            finally:
                conn.close()

        return _run_command_with_timeout(client, chat_id, cmd_name, _wrapped)

    if cmd == "/mt_status":
        ok, body = _run_mtproxy_query("mt_status", lambda conn: current_status_text(conn, mt_cfg))
        if ok and isinstance(body, str):
            client.send_message(chat_id, truncate_for_telegram(body))
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
                        top_n=mt_cfg.daily_report_top_n,
                    ),
                ),
            )
            if not ok or not isinstance(payload, tuple):
                return
            rows, cap = payload
            ok2, _ = _run_command_with_timeout(
                client,
                chat_id,
                "mt_today",
                lambda: generate_mtproxy_chart(rows, out, title=f"MTProxy Load - {time.strftime('%d.%m.%Y')}"),
                known_exceptions=(ImportError,),
            )
            if ok2:
                client.send_photo(chat_id, out, caption=cap)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return
    if cmd == "/mt_threshold":
        parts = text.split()
        if len(parts) != 3:
            client.send_message(chat_id, "Usage: /mt_threshold <warning|critical> <value>")
            return
        param = parts[1].strip().lower()
        try:
            value = int(parts[2])
        except ValueError:
            client.send_message(chat_id, "Invalid value. Must be integer.")
            return
        ok, msg = _run_mtproxy_query("mt_threshold", lambda conn: update_threshold(conn, param, value))
        if ok and isinstance(msg, str):
            client.send_message(chat_id, msg[:2000])


def _handle_vless(client: TelegramClient, chat_id: str, text: str, env_file: Path) -> None:
    parts = text.split()
    if len(parts) > 2:
        client.send_message(chat_id, "Usage: /vless_delta [--since-last-sent]")
        return
    mode_flag = parts[1].strip().lower() if len(parts) == 2 else ""
    since_last = bool(mode_flag) and mode_flag in _VLESS_DELTA_SINCE_LAST_FLAGS
    if mode_flag and not since_last:
        client.send_message(
            chat_id,
            "Unknown flag for /vless_delta. Usage: /vless_delta [--since-last-sent]",
        )
        return

    def _vless() -> None:
        if since_last:
            run_since_last_sent_with_telegram(env_file)
        else:
            run_daily_with_telegram(env_file)

    _run_command_with_timeout(client, chat_id, "vless_delta", _vless, known_exceptions=(VlessReportError,))


def _handle_cake_bw(client: TelegramClient, chat_id: str, text: str, env_file: Path) -> None:
    parts = text.split()
    if len(parts) not in (2, 3):
        client.send_message(chat_id, "Usage: /cake_bw <mbit> [--force]")
        return
    force_mode = len(parts) == 3 and parts[2].strip().lower() == "--force"
    if len(parts) == 3 and not force_mode:
        client.send_message(chat_id, "Unknown flag. Usage: /cake_bw <mbit> [--force]")
        return
    try:
        new_max_rate = int(parts[1])
    except ValueError:
        client.send_message(chat_id, "Invalid value. Must be integer Mbit.")
        return
    if new_max_rate <= 0:
        client.send_message(chat_id, "Invalid value. Must be > 0.")
        return
    raw_env = _parse_env_map(env_file)
    iface = raw_env.get("SHAPER_IFACE", "ens3").strip() or "ens3"
    min_rate = int(raw_env.get("SHAPER_MIN_RATE_MBIT", "10") or "10")
    if new_max_rate < min_rate:
        client.send_message(chat_id, f"Rejected: {new_max_rate}M is below SHAPER_MIN_RATE_MBIT={min_rate}M.")
        return
    _upsert_env_key(env_file, "SHAPER_MAX_RATE_MBIT", str(new_max_rate))
    if force_mode:
        ok, msg = _run_command_with_timeout(
            client,
            chat_id,
            "cake_bw --force",
            lambda: _apply_global_cake_limit(iface=iface, rate_mbit=new_max_rate),
        )
        if ok and isinstance(msg, str):
            client.send_message(chat_id, f"Updated SHAPER_MAX_RATE_MBIT={new_max_rate}M.\n{msg}")
        return
    client.send_message(
        chat_id,
        f"Updated SHAPER_MAX_RATE_MBIT={new_max_rate}M. Shaper timer will apply on next run.",
    )
