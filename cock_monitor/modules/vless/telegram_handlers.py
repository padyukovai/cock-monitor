"""VLESS module tick + Telegram handlers.

Domain logic lives in cock_monitor.services (shared kernel); this module owns orchestration.
"""

from __future__ import annotations

from pathlib import Path

from cock_monitor.platform.telegram.handler_utils import TelegramHandlerContext, run_command_with_timeout
from cock_monitor.services.vless_report import (
    VlessReportError,
    run_daily_with_telegram,
    run_since_last_sent_with_telegram,
)

_VLESS_DELTA_SINCE_LAST_FLAGS = {"--since-last-sent", "--since-last"}


def run_vless_daily_tick(env_file: Path, *, dry_run: bool = False) -> int:
    if dry_run:
        return 0
    from cock_monitor.services.vless_report import run as vless_run

    return vless_run(["--env-file", str(env_file), "--send-telegram", "--mode", "daily"])


def handle_vless_delta(ctx: TelegramHandlerContext) -> None:
    parts = ctx.text.split()
    if len(parts) > 2:
        ctx.client.send_message(ctx.chat_id, "Usage: /vless_delta [--since-last-sent]")
        return
    mode_flag = parts[1].strip().lower() if len(parts) == 2 else ""
    since_last = bool(mode_flag) and mode_flag in _VLESS_DELTA_SINCE_LAST_FLAGS
    if mode_flag and not since_last:
        ctx.client.send_message(
            ctx.chat_id,
            "Unknown flag for /vless_delta. Usage: /vless_delta [--since-last-sent]",
        )
        return

    def _vless() -> None:
        if since_last:
            run_since_last_sent_with_telegram(ctx.env_file)
        else:
            run_daily_with_telegram(ctx.env_file)

    run_command_with_timeout(
        ctx.client,
        ctx.chat_id,
        "vless_delta",
        _vless,
        known_exceptions=(VlessReportError,),
    )
