from __future__ import annotations

from mtproxy_module.repository import connect_db, init_schema

from telegram_bot.config import BotConfig
from telegram_bot.handlers import handle_update
from telegram_bot.offset_store import read_offset, write_offset
from telegram_bot.status_provider import SubprocessStatusProvider
from telegram_bot.telegram_client import TelegramClient


def poll_once(cfg: BotConfig) -> None:
    client = TelegramClient(cfg.bot_token)
    store_path = cfg.offset_file
    next_off = read_offset(store_path)
    script = cfg.monitor_home / "bin" / "cock-status.sh"
    provider = SubprocessStatusProvider(
        env_file=cfg.env_file,
        cock_status_sh=script,
    )
    mtproxy_conn = None
    if cfg.mtproxy.enabled:
        mtproxy_conn = connect_db(cfg.mtproxy.db_path)
        init_schema(mtproxy_conn)
    while True:
        updates = client.get_updates(next_off, timeout=0)
        if not updates:
            break
        max_id = max(int(u["update_id"]) for u in updates)
        for u in updates:
            handle_update(
                u,
                allowed_chat_id=cfg.chat_id,
                client=client,
                status_provider=provider,
                env_file=cfg.env_file,
                mtproxy_cfg=cfg.mtproxy,
                mtproxy_conn=mtproxy_conn,
            )
        next_off = max_id + 1
        write_offset(store_path, next_off)
    if mtproxy_conn is not None:
        mtproxy_conn.close()
