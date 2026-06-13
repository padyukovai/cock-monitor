#!/usr/bin/env bash
# Uninstall cock-monitor v2 (and legacy units).
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON=python3
exec "${PYTHON}" -m cock_monitor uninstall "$@"
