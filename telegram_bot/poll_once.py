from __future__ import annotations

import time

from mtproxy_module.repository import connect_db, init_schema

from telegram_bot.config import BotConfig
from telegram_bot.handlers import handle_update
from telegram_bot.offset_store import read_offset, write_offset
from telegram_bot.status_provider import PythonStatusProvider
from telegram_bot.telegram_client import TelegramClient


def poll_once(cfg: BotConfig) -> None:
    client = TelegramClient(cfg.bot_token)
    store_path = cfg.offset_file
    next_off = read_offset(store_path)
    provider = PythonStatusProvider(
        env_file=cfg.env_file,
    )
    mtproxy_conn = None
    if cfg.mtproxy.enabled:
        mtproxy_conn = connect_db(cfg.mtproxy.db_path)
        init_schema(mtproxy_conn)
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
                mtproxy_conn=mtproxy_conn,
            )
            last_processed_id = max(last_processed_id, update_id)
            processed_updates += 1
            if processed_updates >= cfg.max_updates_per_run:
                break
            if time.monotonic() - started_at >= cfg.max_seconds_per_run:
                break
        next_off = last_processed_id + 1
        write_offset(store_path, next_off)
    if mtproxy_conn is not None:
        mtproxy_conn.close()
