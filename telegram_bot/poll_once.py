from __future__ import annotations

import time

from telegram_bot.config import BotConfig
from telegram_bot.handlers import bot_commands, handle_update
from telegram_bot.offset_store import read_offset, write_offset
from telegram_bot.status_provider import PythonStatusProvider
from telegram_bot.telegram_client import TelegramClient


def poll_once(cfg: BotConfig) -> None:
    client = TelegramClient(cfg.bot_token)
    try:
        client.set_my_commands(bot_commands(mtproxy_enabled=bool(cfg.mtproxy and cfg.mtproxy.enabled)))
    except RuntimeError:
        # Command menu setup failure should not block command polling.
        pass
    store_path = cfg.offset_file
    next_off = read_offset(store_path)
    provider = PythonStatusProvider(
        env_file=cfg.env_file,
    )
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
                status_provider=provider,
                env_file=cfg.env_file,
                mtproxy_cfg=cfg.mtproxy,
            )
            last_processed_id = max(last_processed_id, update_id)
            processed_updates += 1
            if processed_updates >= cfg.max_updates_per_run:
                break
            if time.monotonic() - started_at >= cfg.max_seconds_per_run:
                break
        next_off = last_processed_id + 1
        write_offset(store_path, next_off)
