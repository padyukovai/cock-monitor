"""Shim — re-export platform telegram client for tests and legacy imports."""

from cock_monitor.platform.telegram import telegram_client as _impl
from cock_monitor.platform.telegram.telegram_client import DeliveryResult, TelegramClient

__all__ = ["TelegramClient", "DeliveryResult", "_impl"]
