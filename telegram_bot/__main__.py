from __future__ import annotations

import argparse
import sys
from pathlib import Path

from telegram_bot.config import BotConfig
from telegram_bot.poll_once import poll_once


def main() -> int:
    parser = argparse.ArgumentParser(
        description="cock-monitor Telegram command handler (timer-driven)",
    )
    parser.add_argument(
        "--poll-once",
        action="store_true",
        help="Run one getUpdates sweep (may loop until queue empty) then exit",
    )
    parser.add_argument(
        "env_file",
        nargs="?",
        default="/etc/cock-monitor.env",
        help="Path to env file (default: /etc/cock-monitor.env)",
    )
    args = parser.parse_args()
    if not args.poll_once:
        parser.error("the following arguments are required: --poll-once")
    env_path = Path(args.env_file)
    try:
        cfg = BotConfig.from_env_file(env_path)
    except (OSError, ValueError) as e:
        print(f"telegram_bot: {e}", file=sys.stderr)
        return 1
    try:
        poll_once(cfg)
    except RuntimeError as e:
        print(f"telegram_bot: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
