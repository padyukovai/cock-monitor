"""CLI for daily conntrack chart generation and optional Telegram send."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from telegram_bot.telegram_client import TelegramClient

from cock_monitor.env import merge_env_into_process, parse_env_file
from cock_monitor.services.daily_chart import run_daily_chart


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor daily metrics chart")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/cock-monitor.env"),
        help="Env file with METRICS_DB and optional Telegram vars",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=0,
        help="Window in hours (0 = use DAILY_CHART_HOURS from env or 24)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write PNG to this path",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send chart with caption via Telegram (needs token/chat in env)",
    )
    args = parser.parse_args(argv)

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-daily-chart: env file not found: {env_path}", file=sys.stderr)
        return 1

    raw = parse_env_file(env_path)
    merge_env_into_process(raw)

    out_path = args.output
    if out_path is None:
        out_path = Path(os.environ.get("TMPDIR", "/tmp")) / "cock-monitor-daily.png"

    try:
        caption = run_daily_chart(env_path, out_path, hours=args.hours)
    except FileNotFoundError:
        print(f"cock-daily-chart: env file not found: {env_path}", file=sys.stderr)
        return 1
    except ImportError as e:
        print(
            "cock-daily-chart: matplotlib required "
            "(e.g. apt install python3-matplotlib)",
            file=sys.stderr,
        )
        print(str(e), file=sys.stderr)
        return 1
    except RuntimeError as e:
        msg = str(e)
        if "database not ready" in msg:
            print(f"cock-daily-chart: {msg}", file=sys.stderr)
        elif msg.startswith("sqlite:"):
            print(f"cock-daily-chart: {msg}", file=sys.stderr)
        else:
            print(f"cock-daily-chart: plot failed: {e}", file=sys.stderr)
        return 1

    if args.send_telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            print(
                "cock-daily-chart: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required",
                file=sys.stderr,
            )
            return 1
        client = TelegramClient(token)
        try:
            client.send_photo(chat, out_path, caption=caption)
        except RuntimeError as e:
            print(f"cock-daily-chart: {e}", file=sys.stderr)
            return 1

    return 0
