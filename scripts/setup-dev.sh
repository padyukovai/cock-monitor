#!/usr/bin/env bash
# Bootstrap .venv with dev dependencies (pytest, ruff).
# Uses uv when available (WSL often lacks python3-venv / ensurepip).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RECREATE=0
if [[ "${1:-}" == "--recreate" ]]; then
  RECREATE=1
fi

need_create=0
if [[ ! -x .venv/bin/python ]]; then
  need_create=1
elif ! .venv/bin/pytest --version &>/dev/null; then
  need_create=1
fi

if [[ $RECREATE -eq 1 ]] || [[ $need_create -eq 1 ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv --python python3 --clear
  else
    python3 -m venv .venv
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[dev]" --python .venv/bin/python
else
  .venv/bin/python -m pip install -U pip wheel
  .venv/bin/python -m pip install -e ".[dev]"
fi

echo "Dev venv ready:"
echo "  .venv/bin/pytest"
echo "  .venv/bin/ruff check cock_monitor tests"
