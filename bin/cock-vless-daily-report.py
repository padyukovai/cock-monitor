#!/usr/bin/env python3
"""Build and send VLESS traffic reports from 3x-ui sqlite counters."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cock_monitor.services.vless_report import main

if __name__ == "__main__":
    raise SystemExit(main())
