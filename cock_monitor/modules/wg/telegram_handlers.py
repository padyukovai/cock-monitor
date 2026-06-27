"""WireGuard module Telegram handlers."""

from __future__ import annotations

from cock_monitor.modules.wg.service import wg_status_text
from cock_monitor.platform.telegram.handler_utils import (
    TelegramHandlerContext,
    run_command_with_timeout,
    truncate_for_telegram,
)


def handle_wg_status(ctx: TelegramHandlerContext) -> None:
    ok, body = run_command_with_timeout(
        ctx.client,
        ctx.chat_id,
        "wg_status",
        lambda: wg_status_text(ctx.env_file),
    )
    if ok and isinstance(body, str):
        ctx.client.send_message(ctx.chat_id, truncate_for_telegram(body))
