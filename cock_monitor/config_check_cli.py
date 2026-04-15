"""CLI for config validation and diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_ENV_FILE


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor config validator")
    parser.add_argument(
        "env_file",
        nargs="?",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to env file (default: {DEFAULT_ENV_FILE})",
    )
    args = parser.parse_args(argv)

    path = args.env_file.expanduser().resolve()
    if not path.is_file():
        print(f"config-check: env file not found: {path}", file=sys.stderr)
        return 1

    try:
        loaded = load_config(path)
    except OSError as e:
        print(f"config-check: cannot read env file {path}: {e}", file=sys.stderr)
        return 1

    for w in loaded.validation.warnings:
        print(f"warn: {w}")
    for e in loaded.validation.errors:
        print(f"ERROR: {e}")

    if loaded.validation.ok:
        print(f"ok: config is valid ({path})")
        return 0
    return 1
