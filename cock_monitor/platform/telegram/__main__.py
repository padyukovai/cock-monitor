"""Telegram bot CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cock_monitor.platform.telegram.config import BotConfig
from cock_monitor.platform.telegram.poll_once import poll_once


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor Telegram command handler")
    parser.add_argument("--poll-once", action="store_true", help="Run one getUpdates sweep")
    parser.add_argument("env_file", nargs="?", default="/etc/cock-monitor.env")
    args = parser.parse_args()
    if not args.poll_once:
        parser.error("--poll-once is required")
    env_path = Path(args.env_file)
    try:
        cfg = BotConfig.from_env_file(env_path)
    except (OSError, ValueError) as e:
        print(f"telegram: {e}", file=sys.stderr)
        return 1
    try:
        poll_once(cfg)
    except RuntimeError as e:
        print(f"telegram: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
