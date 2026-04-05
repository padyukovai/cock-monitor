#!/usr/bin/env bash
# Print full conntrack / monitor status to stdout (for Telegram /status and humans).
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/conntrack-metrics.sh
source "${_COCK_REPO_ROOT}/lib/conntrack-metrics.sh"

usage() {
  echo "Usage: ENV_FILE=/path/to.env $0" >&2
  echo "   or: $0 /path/to.env" >&2
  exit 2
}

resolve_env_file() {
  if [[ -n "${1:-}" ]]; then
    printf '%s' "$1"
  elif [[ -n "${ENV_FILE:-}" ]]; then
    printf '%s' "$ENV_FILE"
  else
    return 1
  fi
}

load_env_file() {
  local f=$1
  [[ -f "$f" ]] || {
    echo "cock-status: config not found: $f" >&2
    exit 1
  }
  set -a
  # shellcheck disable=SC1090
  source "$f"
  set +a
}

main() {
  local env_path
  env_path=$(resolve_env_file "${1:-}") || usage
  load_env_file "$env_path"
  apply_defaults

  if [[ "$CHECK_CONNTRACK_FILL" == "1" ]]; then
    compute_fill_severity || exit 1
  fi

  format_full_status_text
}

main "$@"
