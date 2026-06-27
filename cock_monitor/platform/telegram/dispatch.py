"""Unified Telegram command dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cock_monitor.config_loader import load_config
from cock_monitor.platform.registry import get_registry, parse_enabled_modules
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.handler_utils import TelegramHandlerContext


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

    registry = get_registry()
    spec = registry.telegram_handler_for(cmd, raw_env)
    if spec is None or spec.handler is None:
        return

    ctx = TelegramHandlerContext(
        client=client,
        chat_id=chat_id,
        cmd=cmd,
        text=text,
        env_file=env_file,
        raw_env=raw_env,
    )
    spec.handler(ctx)
