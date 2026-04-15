#!/usr/bin/env python3
"""CLI wrapper for VLESS report use-case."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

from cock_monitor.services.vless_report_use_case import (
    VlessReportError,
    run_vless_report_use_case,
)


def run_vless_report(
    env_file: Path,
    *,
    mode: Literal["since-last-sent", "daily"],
    send_telegram: bool,
    dry_run: bool,
) -> None:
    run_vless_report_use_case(
        env_file=env_file,
        mode=mode,
        send_telegram=send_telegram,
        dry_run=dry_run,
    )


def run_since_last_sent_with_telegram(env_file: Path) -> None:
    """On-demand /vless_delta endpoint for Telegram handlers."""
    run_vless_report(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor VLESS daily usage report")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/cock-monitor.env"),
        help="Env file with XUI_DB_PATH, METRICS_DB and optional Telegram vars",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send report to Telegram (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)",
    )
    parser.add_argument(
        "--mode",
        choices=("since-last-sent", "daily"),
        default="since-last-sent",
        help="Report mode: since-last-sent (default) or daily (D vs D-1 in report TZ)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without Telegram send",
    )
    args = parser.parse_args(argv)
    try:
        run_vless_report(
            args.env_file,
            mode=args.mode,
            send_telegram=args.send_telegram,
            dry_run=args.dry_run,
        )
    except VlessReportError as e:
        print(f"cock-vless-daily-report: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
