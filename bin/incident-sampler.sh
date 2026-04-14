#!/usr/bin/env bash
# Periodic incident sampler: network/DNS/conntrack/tcp snapshots to JSONL + optional Telegram alerts.
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/incident-metrics.sh
source "${_COCK_REPO_ROOT}/lib/incident-metrics.sh"

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
    echo "incident-sampler: config not found: $f" >&2
    exit 1
  }
  set -a
  # shellcheck disable=SC1090
  source "$f"
  set +a
}

state_load() {
  state_last_level="OK"
  state_last_alert_ts=0
  state_dns_fail_streak=0
  incident_active=0
  incident_start_ts=0
  incident_peak_level="OK"
  [[ -f "$INCIDENT_STATE_FILE" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    key=${line%%=*}
    value=${line#*=}
    case "$key" in
      last_level) state_last_level=$value ;;
      last_alert_ts) state_last_alert_ts=$value ;;
      dns_fail_streak) state_dns_fail_streak=$value ;;
      incident_active) incident_active=$value ;;
      incident_start_ts) incident_start_ts=$value ;;
      incident_peak_level) incident_peak_level=$value ;;
    esac
  done <"$INCIDENT_STATE_FILE"
  incident_is_int "$state_last_alert_ts" || state_last_alert_ts=0
  incident_is_int "$state_dns_fail_streak" || state_dns_fail_streak=0
  incident_is_int "$incident_active" || incident_active=0
  incident_is_int "$incident_start_ts" || incident_start_ts=0
  [[ -n "$incident_peak_level" ]] || incident_peak_level="OK"
}

state_save() {
  local dir tmp
  dir=$(dirname "$INCIDENT_STATE_FILE")
  mkdir -p "$dir"
  tmp=$(mktemp "${dir}/.incident-state.XXXXXX")
  {
    printf 'last_level=%s\n' "$state_last_level"
    printf 'last_alert_ts=%s\n' "$state_last_alert_ts"
    printf 'dns_fail_streak=%s\n' "$state_dns_fail_streak"
    printf 'incident_active=%s\n' "$incident_active"
    printf 'incident_start_ts=%s\n' "$incident_start_ts"
    printf 'incident_peak_level=%s\n' "$incident_peak_level"
  } >"$tmp"
  mv "$tmp" "$INCIDENT_STATE_FILE"
}

send_telegram() {
  local text=$1
  local parse_mode=${2:-}
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY_RUN] incident telegram:"
    [[ -n "$parse_mode" ]] && echo "parse_mode=${parse_mode}"
    echo "$text"
    return 0
  fi
  local token="${TELEGRAM_BOT_TOKEN:-}"
  local chat="${TELEGRAM_CHAT_ID:-}"
  [[ -n "$token" && -n "$chat" ]] || return 0
  local url="https://api.telegram.org/bot${token}/sendMessage"
  local out http
  out=$(mktemp)
  if [[ -n "$parse_mode" ]]; then
    http=$(curl -sS -o "$out" -w '%{http_code}' -X POST "$url" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "disable_web_page_preview=true" \
      --data-urlencode "parse_mode=${parse_mode}" \
      --data-urlencode "text=${text}" || true)
  else
    http=$(curl -sS -o "$out" -w '%{http_code}' -X POST "$url" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "disable_web_page_preview=true" \
      --data-urlencode "text=${text}" || true)
  fi
  if [[ "$http" != "200" ]]; then
    echo "incident-sampler: telegram http=${http}" >&2
  fi
  rm -f "$out"
}

build_json_line() {
  local ts_iso=$1 ts_epoch=$2 host=$3 level=$4
  printf '{"ts":"%s","ts_epoch":%s,"host":%s,"sampler":"incident-sampler","version":"1","level":"%s","ping":%s,"ping_groups":%s,"dns":{"host":%s,"ok":%s,"latency_ms":%s,"error":%s},"conntrack":{"count":%s,"max":%s,"fill_pct":%s},"tcp":{"estab":%s,"syn_recv":%s,"time_wait":%s},"tcp_probe":{"enabled":%s,"targets":{"local":%s,"external":%s},"totals":{"all":{"total":%s,"fails":%s},"local":{"total":%s,"fails":%s},"external":{"total":%s,"fails":%s}},"checks":%s},"load1":%s,"mem_avail_kb":%s,"units":%s}\n' \
    "$ts_iso" "$ts_epoch" "$(incident_json_quote "$host")" "$level" "$INCIDENT_PING_JSON" \
    "$INCIDENT_PING_GROUPS_JSON" \
    "$(incident_json_quote "$INCIDENT_DNS_HOST")" "$INCIDENT_DNS_OK" "$INCIDENT_DNS_LATENCY_MS" "$(incident_json_quote "$INCIDENT_DNS_ERROR")" \
    "$INCIDENT_CONNTRACK_COUNT" "$INCIDENT_CONNTRACK_MAX" "$INCIDENT_CONNTRACK_FILL_PCT" \
    "$INCIDENT_TCP_ESTAB" "$INCIDENT_TCP_SYN_RECV" "$INCIDENT_TCP_TIME_WAIT" \
    "$INCIDENT_TCP_PROBE_ENABLED" "$(incident_json_quote "$INCIDENT_TCP_PROBE_LOCAL_TARGET_EFF")" "$(incident_json_quote "$INCIDENT_TCP_PROBE_EXTERNAL_TARGET_EFF")" \
    "$INCIDENT_TCP_PROBE_TOTAL" "$INCIDENT_TCP_PROBE_FAILS" \
    "$INCIDENT_TCP_PROBE_LOCAL_TOTAL" "$INCIDENT_TCP_PROBE_LOCAL_FAILS" \
    "$INCIDENT_TCP_PROBE_EXTERNAL_TOTAL" "$INCIDENT_TCP_PROBE_EXTERNAL_FAILS" "$INCIDENT_TCP_PROBE_JSON" \
    "$INCIDENT_LOAD1" "$INCIDENT_MEM_AVAIL_KB" "$INCIDENT_UNITS_JSON"
}

compute_level() {
  local level="OK"
  if (( INCIDENT_CONNTRACK_FILL_PCT >= INCIDENT_CONNTRACK_CRIT_PCT )); then
    level="CRIT"
  elif (( INCIDENT_TCP_PROBE_ENABLED == 1 )) && (( INCIDENT_TCP_PROBE_CRIT_FAILS > 0 )) && (( INCIDENT_TCP_PROBE_FAILS >= INCIDENT_TCP_PROBE_CRIT_FAILS )); then
    level="CRIT"
  elif (( INCIDENT_CONNTRACK_FILL_PCT >= INCIDENT_CONNTRACK_WARN_PCT )) || (( INCIDENT_PING_MAX_LOSS >= INCIDENT_PING_LOSS_WARN_PCT )) || (( state_dns_fail_streak >= INCIDENT_DNS_FAIL_STREAK_WARN )) || (( INCIDENT_TCP_PROBE_ENABLED == 1 && INCIDENT_TCP_PROBE_FAILS >= INCIDENT_TCP_PROBE_WARN_FAILS )); then
    level="WARN"
  fi
  printf '%s' "$level"
}

maybe_alert() {
  local now_ts=$1 level=$2
  [[ "$INCIDENT_ALERT_ENABLE" == "1" ]] || return 0
  local changed=0 cooldown_due=0
  [[ "$level" != "$state_last_level" ]] && changed=1
  if (( now_ts - state_last_alert_ts >= INCIDENT_ALERT_COOLDOWN_SEC )); then
    cooldown_due=1
  fi
  if (( changed == 1 || cooldown_due == 1 )) && [[ "$level" != "OK" || "$state_last_level" != "OK" ]]; then
    local text
    text="incident-sampler ${level} on $(incident_hostname)
time: $(incident_now_iso_utc)
conntrack: ${INCIDENT_CONNTRACK_COUNT}/${INCIDENT_CONNTRACK_MAX} (${INCIDENT_CONNTRACK_FILL_PCT}%)
ping max loss: ${INCIDENT_PING_MAX_LOSS}%
dns: ok=${INCIDENT_DNS_OK} streak=${state_dns_fail_streak} err=${INCIDENT_DNS_ERROR}
tcp: estab=${INCIDENT_TCP_ESTAB} syn_recv=${INCIDENT_TCP_SYN_RECV} tw=${INCIDENT_TCP_TIME_WAIT}
tcp-probe all: ${INCIDENT_TCP_PROBE_FAILS}/${INCIDENT_TCP_PROBE_TOTAL} failed
tcp-probe local: ${INCIDENT_TCP_PROBE_LOCAL_FAILS}/${INCIDENT_TCP_PROBE_LOCAL_TOTAL} target=${INCIDENT_TCP_PROBE_LOCAL_TARGET_EFF}
tcp-probe external: ${INCIDENT_TCP_PROBE_EXTERNAL_FAILS}/${INCIDENT_TCP_PROBE_EXTERNAL_TOTAL} target=${INCIDENT_TCP_PROBE_EXTERNAL_TARGET_EFF}"
    send_telegram "$text"
    state_last_alert_ts=$now_ts
  fi
}

# Track active incident window and send HTML post-mortem on recovery (WARN/CRIT -> OK).
incident_track_and_postmortem() {
  local old_level=$1 new_level=$2 now_ts=$3 host=$4
  if [[ "$old_level" == "OK" ]] && [[ "$new_level" != "OK" ]]; then
    incident_active=1
    incident_start_ts=$now_ts
    incident_peak_level=$new_level
  elif [[ "$old_level" != "OK" ]] && [[ "$new_level" != "OK" ]]; then
    incident_active=1
    if [[ "$new_level" == "CRIT" ]]; then
      incident_peak_level="CRIT"
    elif [[ "$incident_peak_level" != "CRIT" ]] && [[ "$new_level" == "WARN" ]]; then
      incident_peak_level="WARN"
    fi
  elif [[ "$old_level" != "OK" ]] && [[ "$new_level" == "OK" ]]; then
    if (( incident_active == 1 )); then
      if [[ "${INCIDENT_POSTMORTEM_ENABLE:-1}" == "1" ]] && command -v python3 >/dev/null 2>&1; then
        local pm
        pm=$(python3 "${_COCK_REPO_ROOT}/bin/incident-postmortem.py" \
          "$incident_start_ts" "$now_ts" "$INCIDENT_LOG_DIR" "$host" "$incident_peak_level" 2>/dev/null \
          || printf '%s\n' "<i>incident-postmortem.py failed</i>")
        send_telegram "$pm" "HTML"
      fi
      incident_active=0
      incident_start_ts=0
      incident_peak_level="OK"
    fi
  fi
}

main() {
  local env_file
  env_file=$(resolve_env_file "${1:-}") || usage
  load_env_file "$env_file"
  incident_apply_defaults

  [[ "$INCIDENT_SAMPLER_ENABLE" == "1" ]] || exit 0

  local now_ts ts_iso host logfile level line
  now_ts=$(incident_now_epoch)
  ts_iso=$(incident_now_iso_utc)
  host=$(incident_hostname)
  logfile="${INCIDENT_LOG_DIR}/incident-$(date -u +%Y%m%d).jsonl"

  mkdir -p "$INCIDENT_LOG_DIR"
  state_load

  incident_collect_ping
  incident_collect_ping_groups
  incident_collect_dns
  incident_collect_conntrack
  incident_collect_ss
  incident_collect_tcp_probes
  incident_collect_load_mem
  incident_collect_units

  if (( INCIDENT_DNS_OK == 1 )); then
    state_dns_fail_streak=0
  else
    state_dns_fail_streak=$((state_dns_fail_streak + 1))
  fi

  level=$(compute_level)
  line=$(build_json_line "$ts_iso" "$now_ts" "$host" "$level")
  # Command substitution strips trailing newlines; always emit one JSON line per tick.
  printf '%s\n' "$line" >>"$logfile"

  incident_track_and_postmortem "$state_last_level" "$level" "$now_ts" "$host"
  maybe_alert "$now_ts" "$level"
  state_last_level="$level"
  state_save
}

main "$@"
