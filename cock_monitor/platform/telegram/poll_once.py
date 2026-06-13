"""One-shot Telegram poll."""

from __future__ import annotations

import time

from cock_monitor.platform.registry import get_registry
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.platform.telegram.config import BotConfig
from cock_monitor.platform.telegram.dispatch import handle_update
from cock_monitor.platform.telegram.offset_store import read_offset, write_offset


def poll_once(cfg: BotConfig) -> None:
    client = TelegramClient(cfg.bot_token)
    registry = get_registry()
    env_map = dict(getattr(cfg, "env", {}))
    if not env_map and cfg.env_file.is_file():
        from cock_monitor.platform.config import load_runtime_env

        env_map = load_runtime_env(cfg.env_file)
    if not env_map:
        env_map = {"ENABLED_MODULES": "core"}
    try:
        cmds = [(c.name, c.help_text) for c in registry.telegram_commands(env_map)]
        client.set_my_commands(cmds)
    except RuntimeError:
        pass
    store_path = cfg.offset_file
    next_off = read_offset(store_path)
    started_at = time.monotonic()
    processed_updates = 0
    while True:
        if processed_updates >= cfg.max_updates_per_run:
            break
        if time.monotonic() - started_at >= cfg.max_seconds_per_run:
            break
        updates = client.get_updates(next_off, timeout=0)
        if not updates:
            break
        last_processed_id = next_off - 1
        for u in updates:
            update_id = int(u["update_id"])
            handle_update(
                u,
                allowed_chat_id=cfg.chat_id,
                client=client,
                env_file=cfg.env_file,
            )
            last_processed_id = max(last_processed_id, update_id)
            processed_updates += 1
            if processed_updates >= cfg.max_updates_per_run:
                break
            if time.monotonic() - started_at >= cfg.max_seconds_per_run:
                break
        next_off = last_processed_id + 1
        write_offset(store_path, next_off)
