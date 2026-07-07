"""Core module Telegram handlers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cock_monitor.modules.core.charts import run_core_chart, run_leak_chart
from cock_monitor.modules.core.status import build_core_status
from cock_monitor.modules.wg.service import wg_status_text
from cock_monitor.platform.registry import module_enabled
from cock_monitor.platform.telegram.handler_utils import (
    TelegramHandlerContext,
    run_command_with_timeout,
    truncate_for_telegram,
)


def handle_status(ctx: TelegramHandlerContext) -> None:
    ok, body = run_command_with_timeout(
        ctx.client,
        ctx.chat_id,
        "status",
        lambda: build_core_status(ctx.env_file),
    )
    if ok and isinstance(body, str):
        extra = ""
        if module_enabled("wg", ctx.raw_env):
            try:
                extra = "\n\n--- WireGuard ---\n" + wg_status_text(ctx.env_file)
            except OSError:
                pass
        ctx.client.send_message(ctx.chat_id, truncate_for_telegram(body + extra))


def handle_chart(ctx: TelegramHandlerContext) -> None:
    parts = ctx.text.split(None, 1)
    args = parts[1].strip().lower() if len(parts) > 1 else ""
    use_leak = args in ("leak", "leak24", "memory")
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    try:
        os.close(fd)
        out = Path(tmp_path)
        if use_leak:
            ok, caption = run_command_with_timeout(
                ctx.client,
                ctx.chat_id,
                "chart leak",
                lambda: run_leak_chart(ctx.env_file, out),
                known_exceptions=(FileNotFoundError, RuntimeError, ImportError),
            )
        else:
            ok, caption = run_command_with_timeout(
                ctx.client,
                ctx.chat_id,
                "chart",
                lambda: run_core_chart(ctx.env_file, out),
                known_exceptions=(FileNotFoundError, RuntimeError, ImportError),
            )
        if ok and isinstance(caption, str):
            ctx.client.send_photo(ctx.chat_id, out, caption=caption)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
