#!/usr/bin/env bash
# Lightweight nf_conntrack fill check + optional conntrack -S stats; Telegram alerts with cooldown.
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/conntrack-metrics.sh
source "${_COCK_REPO_ROOT}/lib/conntrack-metrics.sh"

usage() {
  echo "Usage: ENV_FILE=/path/to.env $0" >&2
  echo "   or: $0 [--dry-run] /path/to.env" >&2
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
    echo "check-conntrack: config not found: $f" >&2
    exit 1
  }
  set -a
  # shellcheck disable=SC1090
  source "$f"
  set +a
}

state_get() {
  local key=$1
  [[ -f "$STATE_FILE" ]] || return 0
  local line
  line=$(grep "^${key}=" "$STATE_FILE" 2>/dev/null | tail -n1) || true
  [[ -n "$line" ]] || return 0
  printf '%s' "${line#*=}"
}

state_write() {
  local dir tmp
  dir=$(dirname "$STATE_FILE")
  mkdir -p "$dir" 2>/dev/null || {
    echo "check-conntrack: cannot create state directory $dir" >&2
    return 1
  }
  tmp=$(mktemp "${dir}/.state.XXXXXX")
  {
    printf 'fill_last_ts=%s\n' "${fill_last_ts}"
    printf 'fill_last_severity=%s\n' "${fill_last_severity}"
    printf 'stats_last_ts=%s\n' "${stats_last_ts}"
  } >"$tmp"
  mv "$tmp" "$STATE_FILE"
}

now_epoch() {
  date +%s
}

send_telegram() {
  local text=$1
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY_RUN] Telegram message:"
    echo "$text"
    return 0
  fi
  local url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
  local out http
  out=$(mktemp)
  http=$(curl -sS -o "$out" -w '%{http_code}' -X POST "$url" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "disable_web_page_preview=true") || {
    rm -f "$out"
    echo "check-conntrack: curl failed" >&2
    return 1
  }
  if [[ "$http" != "200" ]]; then
    echo "check-conntrack: Telegram API HTTP $http" >&2
    cat "$out" >&2 || true
    rm -f "$out"
    return 1
  fi
  rm -f "$out"
  return 0
}

should_send_fill_alert() {
  local current=$1
  local ts_prev sev_prev now
  now=$(now_epoch)
  ts_prev=$fill_last_ts
  sev_prev=$fill_last_severity
  [[ "$ts_prev" =~ ^[0-9]+$ ]] || ts_prev=0
  [[ "$sev_prev" =~ ^[0-2]$ ]] || sev_prev=0

  if [[ "$current" -eq 0 ]]; then
    return 1
  fi

  if [[ "$current" -gt "$sev_prev" ]]; then
    return 0
  fi
  if [[ "$sev_prev" -eq 0 ]]; then
    return 0
  fi
  if ((now - ts_prev >= COOLDOWN_SECONDS)); then
    return 0
  fi
  return 1
}

should_send_stats_alert() {
  local now ts_prev
  now=$(now_epoch)
  ts_prev=$stats_last_ts
  [[ "$ts_prev" =~ ^[0-9]+$ ]] || ts_prev=0
  if ((now - ts_prev >= STATS_COOLDOWN_SECONDS)); then
    return 0
  fi
  return 1
}

main() {
  local dry_run_cli=0
  while [[ "${1:-}" == "--dry-run" ]]; do
    dry_run_cli=1
    shift
  done
  local env_path
  env_path=$(resolve_env_file "${1:-}") || usage
  load_env_file "$env_path"
  apply_defaults
  [[ "$dry_run_cli" -eq 1 ]] && DRY_RUN=1

  if [[ "$DRY_RUN" != "1" ]]; then
    [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] || {
      echo "check-conntrack: TELEGRAM_BOT_TOKEN is required unless DRY_RUN=1" >&2
      exit 1
    }
    [[ -n "${TELEGRAM_CHAT_ID:-}" ]] || {
      echo "check-conntrack: TELEGRAM_CHAT_ID is required unless DRY_RUN=1" >&2
      exit 1
    }
  fi

  local host
  host=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo unknown)

  fill_last_ts=$(state_get fill_last_ts)
  fill_last_severity=$(state_get fill_last_severity)
  [[ "$fill_last_ts" =~ ^[0-9]+$ ]] || fill_last_ts=0
  [[ "$fill_last_severity" =~ ^[0-2]$ ]] || fill_last_severity=0
  stats_last_ts=$(state_get stats_last_ts)
  [[ "$stats_last_ts" =~ ^[0-9]+$ ]] || stats_last_ts=0

  local fill_severity=0
  local stats_note="" drop_sum if_sum

  if [[ "$CHECK_CONNTRACK_FILL" == "1" ]]; then
    compute_fill_severity || exit 1
    fill_severity=$FILL_SEVERITY

    if [[ "$INCLUDE_CONNTRACK_STATS_LINE" == "1" ]]; then
      stats_note=$(conntrack_stats_line)
    fi

    if [[ "$fill_severity" -eq 0 ]]; then
      fill_last_severity=0
    elif should_send_fill_alert "$fill_severity"; then
      local label body
      if [[ "$fill_severity" -eq 2 ]]; then
        label="CRITICAL"
      else
        label="WARNING"
      fi
      body="${label} conntrack fill on ${host}"$'\n'"${FILL_PCT}% (${FILL_COUNT}/${FILL_MAX}) warn>=${WARN_PERCENT}% crit>=${CRIT_PERCENT}%"
      [[ -n "$stats_note" ]] && body+=$'\n'"${stats_note}"
      send_telegram "$body" || exit 1
      fill_last_ts=$(now_epoch)
      fill_last_severity=$fill_severity
    fi
  fi

  if [[ "$ALERT_ON_STATS" == "1" ]]; then
    if command -v conntrack >/dev/null 2>&1; then
      drop_sum=$(sum_conntrack_stat drop)
      if_sum=$(sum_conntrack_stat insert_failed)
      local fire=0 reason=""
      if [[ "$STATS_DROP_MIN" =~ ^[0-9]+$ && "$STATS_DROP_MIN" -gt 0 && "$drop_sum" -ge "$STATS_DROP_MIN" ]]; then
        fire=1
        reason="drop=${drop_sum} (threshold ${STATS_DROP_MIN})"
      fi
      if [[ "$STATS_INSERT_FAILED_MIN" =~ ^[0-9]+$ && "$STATS_INSERT_FAILED_MIN" -gt 0 && "$if_sum" -ge "$STATS_INSERT_FAILED_MIN" ]]; then
        fire=1
        reason="${reason:+$reason; }insert_failed=${if_sum} (threshold ${STATS_INSERT_FAILED_MIN})"
      fi
      if [[ "$fire" -eq 1 ]]; then
        local stats_body
        stats_body="STATS ${host}"$'\n'"${reason}"$'\n'"$(conntrack_stats_line)"
        if should_send_stats_alert; then
          send_telegram "$stats_body" || exit 1
          stats_last_ts=$(now_epoch)
        fi
      fi
    fi
  fi

  state_write || true

  exit 0
}

main "$@"
