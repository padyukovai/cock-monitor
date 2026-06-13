"""Shim — delegates to platform poll_once, adds test-compatible exports."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cock_monitor.platform.config import load_runtime_env
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.config import BotConfig
from cock_monitor.platform.telegram.dispatch import build_help_text, handle_update
from cock_monitor.platform.telegram.offset_store import read_offset, write_offset
from cock_monitor.platform.telegram import poll_once as _platform_poll_once
from telegram_bot.status_provider import PythonStatusProvider


def _normalize_cfg(cfg: Any) -> BotConfig | Any:
    if isinstance(cfg, BotConfig):
        return cfg
    env_file = Path(getattr(cfg, "env_file", "/etc/cock-monitor.env"))
    env: dict[str, str]
    if hasattr(cfg, "env") and getattr(cfg, "env"):
        env = dict(cfg.env)
    elif env_file.is_file():
        env = load_runtime_env(env_file)
    else:
        env = {"ENABLED_MODULES": "core"}
    return SimpleNamespace(
        bot_token=getattr(cfg, "bot_token", ""),
        chat_id=str(getattr(cfg, "chat_id", "")),
        offset_file=Path(getattr(cfg, "offset_file", env_file.parent / "telegram_offset")),
        env_file=env_file,
        env=env,
        max_updates_per_run=int(getattr(cfg, "max_updates_per_run", 200)),
        max_seconds_per_run=int(getattr(cfg, "max_seconds_per_run", 20)),
    )


def poll_once(cfg: Any) -> None:
    return _platform_poll_once.poll_once(_normalize_cfg(cfg))


__all__ = [
    "poll_once",
    "TelegramClient",
    "BotConfig",
    "handle_update",
    "read_offset",
    "write_offset",
    "PythonStatusProvider",
    "build_help_text",
    "time",
]
