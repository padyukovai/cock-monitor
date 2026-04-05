from __future__ import annotations

from typing import Any

from telegram_bot.status_provider import StatusProvider, truncate_for_telegram
from telegram_bot.telegram_client import TelegramClient


def _command_token(text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first = text.split(None, 1)[0]
    if "@" in first:
        first = first.split("@", 1)[0]
    return first.lower()


HELP_TEXT = (
    "cock-monitor bot: /status — full conntrack status. "
    "Alerts still come from the scheduled check."
)


def handle_update(
    update: dict[str, Any],
    *,
    allowed_chat_id: str,
    client: TelegramClient,
    status_provider: StatusProvider,
) -> None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    if str(chat_id) != str(allowed_chat_id):
        return
    text = msg.get("text")
    if not isinstance(text, str):
        return
    cmd = _command_token(text)
    if cmd is None:
        return
    if cmd in ("/start", "/help"):
        client.send_message(str(chat_id), HELP_TEXT)
        return
    if cmd != "/status":
        return
    ok, body = status_provider.get_status()
    if not ok:
        client.send_message(
            str(chat_id),
            "Status failed:\n" + body[:2000],
        )
        return
    client.send_message(str(chat_id), truncate_for_telegram(body))
