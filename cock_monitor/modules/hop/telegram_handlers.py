"""Hop module Telegram handlers."""

from __future__ import annotations

from cock_monitor.modules.hop.service import hop_status_text
from cock_monitor.platform.telegram.handler_utils import (
    TelegramHandlerContext,
    run_command_with_timeout,
    truncate_for_telegram,
)


def handle_hop_status(ctx: TelegramHandlerContext) -> None:
    ok, body = run_command_with_timeout(
        ctx.client,
        ctx.chat_id,
        "hop_status",
        lambda: hop_status_text(ctx.env_file),
    )
    if ok and isinstance(body, str):
        ctx.client.send_message(ctx.chat_id, truncate_for_telegram(body))
