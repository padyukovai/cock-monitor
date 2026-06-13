"""Compatibility shim — redirects to platform.telegram."""

from cock_monitor.platform.telegram.client import TelegramClient, DeliveryResult
from cock_monitor.platform.telegram.runtime import run_with_timeout

__all__ = ["TelegramClient", "DeliveryResult", "run_with_timeout"]
