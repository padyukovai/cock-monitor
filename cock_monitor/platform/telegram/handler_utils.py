"""Shared helpers for module Telegram command handlers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.runtime import run_with_timeout

BOT_CMD_TIMEOUT_SEC = 120.0
CAPTION_MAX = 4096


@dataclass(frozen=True)
class TelegramHandlerContext:
    client: TelegramClient
    chat_id: str
    cmd: str
    text: str
    env_file: Path
    raw_env: dict[str, str]


def truncate_for_telegram(text: str, limit: int = CAPTION_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def send_cmd_failure(client: TelegramClient, chat_id: str, cmd: str, message: str) -> None:
    client.send_message(chat_id, f"{cmd} failed:\n{message}"[:2000])


def run_command_with_timeout(
    client: TelegramClient,
    chat_id: str,
    cmd: str,
    fn: Callable[[], Any],
    *,
    timeout_sec: float = BOT_CMD_TIMEOUT_SEC,
    known_exceptions: tuple[type[BaseException], ...] = (),
) -> tuple[bool, Any]:
    try:
        return True, run_with_timeout(fn, timeout_sec)
    except FutureTimeout:
        send_cmd_failure(client, chat_id, cmd, f"timed out after {timeout_sec:.0f}s")
    except known_exceptions as e:
        send_cmd_failure(client, chat_id, cmd, str(e))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as e:
        send_cmd_failure(client, chat_id, cmd, str(e))
    return False, None


def upsert_env_key(path: Path, key: str, value: str) -> None:
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
