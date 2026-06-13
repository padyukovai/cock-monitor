#!/usr/bin/env bash
# Legacy wrapper — prints /status text to stdout
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV="${1:-/etc/cock-monitor.env}"
exec "${ROOT}/.venv/bin/python" -c "
from pathlib import Path
from cock_monitor.modules.core.status import build_core_status
print(build_core_status(Path('${ENV}')))
"
