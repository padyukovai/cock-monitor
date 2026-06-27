"""Telegram Bot API client (v2)."""

from cock_monitor.platform.telegram.telegram_client import DeliveryResult, TelegramClient, telegram_client_from_env

__all__ = ["TelegramClient", "DeliveryResult", "telegram_client_from_env"]
