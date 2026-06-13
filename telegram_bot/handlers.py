"""Compatibility handlers shim."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.platform.registry import get_registry
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.dispatch import build_help_text, handle_update as _dispatch
from cock_monitor.platform.telegram.runtime import run_with_timeout
from cock_monitor.services.vless_report import (
    run_daily_with_telegram,
    run_since_last_sent_with_telegram,
)
from telegram_bot.status_provider import StatusProvider, truncate_for_telegram


def bot_commands(*, mtproxy_enabled: bool = False) -> list[tuple[str, str]]:
    env = {"ENABLED_MODULES": "core,mtproxy" if mtproxy_enabled else "core"}
    return [(c.name, c.help_text) for c in get_registry().telegram_commands(env)]


def _help_text(mtproxy_enabled: bool = False) -> str:
    env = {"ENABLED_MODULES": "core,mtproxy" if mtproxy_enabled else "core"}
    return build_help_text(env)


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    status_provider: StatusProvider | None = None,
    env_file: Path | None = None,
    mtproxy_cfg: MtproxyConfig | None = None,
) -> None:
    if env_file is None:
        return
    _dispatch(
        update,
        allowed_chat_id=allowed_chat_id,
        client=client,
        env_file=env_file,
    )


__all__ = [
    "handle_update",
    "bot_commands",
    "truncate_for_telegram",
    "run_with_timeout",
    "run_daily_with_telegram",
    "run_since_last_sent_with_telegram",
]
