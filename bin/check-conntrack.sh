#!/usr/bin/env bash
# Legacy wrapper — use: python -m cock_monitor run core
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV="${1:-/etc/cock-monitor.env}"
exec "${ROOT}/.venv/bin/python" -m cock_monitor run core "${ENV}"
