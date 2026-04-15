#!/usr/bin/env python3
"""Compatibility wrapper for `python -m cock_monitor daily-chart`."""
from __future__ import annotations

from cock_monitor.daily_chart_cli import run


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
