#!/usr/bin/env bash
# Install cock-monitor v2 from current repo clone.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON=python3

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run: sudo bash install/install.sh ..." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl sqlite3 conntrack python3-matplotlib wireguard-tools

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${REPO_ROOT}/.venv"
  "${REPO_ROOT}/.venv/bin/pip" install --upgrade pip wheel
  "${REPO_ROOT}/.venv/bin/pip" install -e "${REPO_ROOT}[chart]"
fi

exec "${PYTHON}" -m cock_monitor install --repo "${REPO_ROOT}" "$@"
