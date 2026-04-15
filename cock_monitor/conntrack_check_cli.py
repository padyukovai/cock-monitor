"""CLI entrypoint for conntrack orchestration use-case."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cock_monitor.services.conntrack_check import run_conntrack_check


def _resolve_env_file(arg_env_file: str | None) -> Path | None:
    if arg_env_file:
        return Path(arg_env_file)
    env_file = os.environ.get("ENV_FILE", "").strip()
    if env_file:
        return Path(env_file)
    return None


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cock_monitor conntrack-check",
        add_help=True,
        description="Run conntrack checks and alerts",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not call Telegram API")
    parser.add_argument("env_file", nargs="?", help="Path to env file (or ENV_FILE)")
    args = parser.parse_args(argv)
    env_file = _resolve_env_file(args.env_file)
    if env_file is None:
        print("Usage: ENV_FILE=/path/to.env check-conntrack", file=sys.stderr)
        print("   or: check-conntrack [--dry-run] /path/to.env", file=sys.stderr)
        return 2
    return run_conntrack_check(env_file.expanduser(), dry_run_override=args.dry_run)
