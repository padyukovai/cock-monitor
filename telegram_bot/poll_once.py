from __future__ import annotations

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
    chart_script = cfg.monitor_home / "bin" / "cock-daily-chart.py"
    provider = SubprocessStatusProvider(
        env_file=cfg.env_file,
        cock_status_sh=script,
    )
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
                chart_script=chart_script,
                env_file=cfg.env_file,
            )
        next_off = max_id + 1
        write_offset(store_path, next_off)
