#!/usr/bin/env bash
# Thin wrapper: all orchestration lives in Python use-case.
set -euo pipefail

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-conntrack: python3 is required" >&2
  exit 1
fi

PYTHONPATH="${_COCK_REPO_ROOT}" exec python3 -m cock_monitor conntrack-check "$@"
