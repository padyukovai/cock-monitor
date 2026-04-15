#!/usr/bin/env python3
"""Build and send VLESS traffic reports from 3x-ui sqlite counters."""
from __future__ import annotations

from cock_monitor.services.vless_report import main

if __name__ == "__main__":
    raise SystemExit(main())
