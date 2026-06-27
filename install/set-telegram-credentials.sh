#!/usr/bin/env bash
# Interactive TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID for /etc/cock-monitor.env (v2).
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/cock-monitor.env}"
REPO_ROOT="${REPO_ROOT:-/opt/cock-monitor}"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run: sudo bash install/set-telegram-credentials.sh" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON}" || ! -x "${PYTHON}" ]]; then
  echo "Python not found (expected ${REPO_ROOT}/.venv/bin/python)" >&2
  exit 1
fi

echo "cock-monitor: set Telegram credentials in ${ENV_FILE}"
echo "(input is not echoed for the token)"
echo

read -r -s -p "TELEGRAM_BOT_TOKEN: " TELEGRAM_BOT_TOKEN
echo
read -r -p "TELEGRAM_CHAT_ID: " TELEGRAM_CHAT_ID

if [[ -z "${TELEGRAM_BOT_TOKEN}" || -z "${TELEGRAM_CHAT_ID}" ]]; then
  echo "Both values are required." >&2
  exit 1
fi

backup="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp -a "${ENV_FILE}" "${backup}"
echo "Backup: ${backup}"

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}" \
  ENV_FILE="${ENV_FILE}" COCK_MONITOR_HOME="${REPO_ROOT}" \
  "${PYTHON}" - <<'PY'
import os
import sys
from pathlib import Path

repo = Path(os.environ.get("COCK_MONITOR_HOME", "/opt/cock-monitor"))
sys.path.insert(0, str(repo))

from cock_monitor.env import parse_env_file
from cock_monitor.platform.config import write_env_file

env_file = Path(os.environ["ENV_FILE"])
token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

env = parse_env_file(env_file)
env["TELEGRAM_BOT_TOKEN"] = token
env["TELEGRAM_CHAT_ID"] = chat_id
write_env_file(env_file, env)
print(f"Updated {env_file}")
PY

if command -v systemctl >/dev/null 2>&1; then
  systemctl start cock-monitor-telegram.service || true
  echo
  echo "Telegram poll triggered. Check:"
  echo "  journalctl -u cock-monitor-telegram.service -n 20 --no-pager"
fi
