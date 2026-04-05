#!/usr/bin/env bash
# Dynamic CPU-aware WAN egress shaper (HTB + CAKE) for VPN ports.
# Reduces VPN bandwidth when CPU load is high to prevent crypto processing bottlenecks.
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "Usage: $0 [--dry-run] /path/to.env" >&2
  echo "   or: ENV_FILE=/path/to.env $0" >&2
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
    echo "cock-cpu-shaper: config not found: $f" >&2
    exit 1
  }
  set -a
  # shellcheck disable=SC1090
  source "$f"
  set +a
}

apply_shaper_defaults() {
  SHAPER_ENABLE="${SHAPER_ENABLE:-0}"
  SHAPER_IFACE="${SHAPER_IFACE:-ens3}"
  SHAPER_VPN_PORTS="${SHAPER_VPN_PORTS:-443,2053,37346}"
  SHAPER_MAX_RATE_MBIT="${SHAPER_MAX_RATE_MBIT:-1000}"
  SHAPER_MIN_RATE_MBIT="${SHAPER_MIN_RATE_MBIT:-10}"
  SHAPER_CPU_TARGET_PCT="${SHAPER_CPU_TARGET_PCT:-85}"
  SHAPER_STEP_DOWN_PCT="${SHAPER_STEP_DOWN_PCT:-15}"
  SHAPER_STEP_UP_PCT="${SHAPER_STEP_UP_PCT:-5}"
  SHAPER_MEASURE_SLEEP_SEC="${SHAPER_MEASURE_SLEEP_SEC:-2}"
  SHAPER_TELEGRAM_ALERTS="${SHAPER_TELEGRAM_ALERTS:-1}"
  SHAPER_TELEGRAM_COOLDOWN_SEC="${SHAPER_TELEGRAM_COOLDOWN_SEC:-60}"
  SHAPER_STATE_FILE="${SHAPER_STATE_FILE:-/var/lib/cock-monitor/cpu_shaper.state}"
  SHAPER_STATUS_FILE="${SHAPER_STATUS_FILE:-/var/lib/cock-monitor/cpu_shaper.status}"
}

now_epoch() {
  date +%s
}

shaper_state_get() {
  local key=$1
  [[ -f "$SHAPER_STATE_FILE" ]] || return 0
  local line
  line=$(grep "^${key}=" "$SHAPER_STATE_FILE" 2>/dev/null | tail -n1) || true
  [[ -n "$line" ]] || return 0
  printf '%s' "${line#*=}"
}

shaper_state_write_kv() {
  local dir tmp
  dir=$(dirname "$SHAPER_STATE_FILE")
  mkdir -p "$dir" 2>/dev/null || return 1
  tmp=$(mktemp "${dir}/.cpu_shaper_state.XXXXXX")
  printf '%s\n' "$1" >"$tmp"
  mv "$tmp" "$SHAPER_STATE_FILE"
}

get_cpu_idle() {
  # Output sum(all), sum(idle+iowait)
  awk '/^cpu / { print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6 }' /proc/stat
}

send_shaper_telegram() {
  local text=$1
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[DRY_RUN] Telegram:" >&2
    echo "$text" >&2
    return 0
  fi
  [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]] || return 0
  local url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
  local out
  out=$(mktemp)
  local http
  http=$(curl -sS -o "$out" -w '%{http_code}' -X POST "$url" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "disable_web_page_preview=true") || {
    rm -f "$out"
    return 1
  }
  rm -f "$out"
  [[ "$http" == "200" ]]
}

tc_teardown() {
  local dev=$1
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[DRY_RUN] tc qdisc del dev $dev root (teardown)"
    return 0
  fi
  tc qdisc del dev "$dev" root 2>/dev/null || true
  # Optionally restore fq for BBR compatibility
  tc qdisc add dev "$dev" root fq 2>/dev/null || true
}

tc_apply() {
  local dev=$1 rate=$2 ports_csv=$3
  
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[DRY_RUN] tc_apply dev=$dev rate=${rate}M ports=$ports_csv"
    return 0
  fi

  if ! tc qdisc show dev "$dev" 2>/dev/null | grep -q 'htb default 20'; then
    # Full tree initialization
    tc qdisc del dev "$dev" root 2>/dev/null || true
    tc qdisc add dev "$dev" root handle 1: htb default 20
    
    # Root class (just a high bound)
    tc class add dev "$dev" parent 1: classid 1:1 htb rate 10000mbit ceil 10000mbit
    # Default class (SSH, apt, etc) - unrestricted
    tc class add dev "$dev" parent 1:1 classid 1:20 htb rate 10000mbit ceil 10000mbit
    tc qdisc add dev "$dev" parent 1:20 handle 20: fq_codel 2>/dev/null || tc qdisc add dev "$dev" parent 1:20 handle 20: fq 2>/dev/null || true
    
    # VPN class - restricted to calculated target rate
    tc class add dev "$dev" parent 1:1 classid 1:10 htb rate "${rate}mbit" ceil "${rate}mbit"
    # CAKE queueing applied ONLY to VPN class to balance traffic evenly between users
    tc qdisc add dev "$dev" parent 1:10 handle 10: cake bandwidth "${rate}mbit" flowblind dual-dsthost
    
    # Link ports to VPN class
    IFS=',' read -ra ports <<< "$ports_csv"
    for p in "${ports[@]}"; do
      p=$(echo "$p" | tr -d '[:space:]')
      [[ "$p" =~ ^[0-9]+$ ]] || continue
      tc filter add dev "$dev" parent 1:0 protocol ip prio 1 u32 match ip sport "$p" 0xffff flowid 1:10
      tc filter add dev "$dev" parent 1:0 protocol ipv6 prio 1 u32 match ip6 sport "$p" 0xffff flowid 1:10
    done
  else
    # Tree exists, just update the rate on the fly
    tc class change dev "$dev" parent 1:1 classid 1:10 htb rate "${rate}mbit" ceil "${rate}mbit"
    tc qdisc replace dev "$dev" parent 1:10 handle 10: cake bandwidth "${rate}mbit" flowblind dual-dsthost
  fi
}

main() {
  local dry=0
  while [[ "${1:-}" == "--dry-run" ]]; do
    dry=1
    shift
  done
  
  local env_path
  env_path=$(resolve_env_file "${1:-}") || usage
  load_env_file "$env_path"
  apply_shaper_defaults
  [[ "$dry" -eq 1 ]] && DRY_RUN=1

  local ts
  ts=$(now_epoch)

  if [[ "$SHAPER_ENABLE" != "1" ]]; then
    tc_teardown "$SHAPER_IFACE" || true
    shaper_state_write_kv "enabled=0\nts=${ts}\niface=${SHAPER_IFACE}"
    echo "SHAPER_ENABLE=0 (teardown attempted)" >"${SHAPER_STATUS_FILE}.tmp" 2>/dev/null && mv "${SHAPER_STATUS_FILE}.tmp" "$SHAPER_STATUS_FILE" 2>/dev/null || true
    exit 0
  fi

  if ! ip link show "$SHAPER_IFACE" &>/dev/null; then
    echo "cock-cpu-shaper: interface not found: $SHAPER_IFACE" >&2
    exit 1
  fi
  
  # Measure CPU
  local tot1 idl1 tot2 idl2 dtot didl cpu_pct
  read -r tot1 idl1 <<< "$(get_cpu_idle)"
  sleep "$SHAPER_MEASURE_SLEEP_SEC"
  read -r tot2 idl2 <<< "$(get_cpu_idle)"
  dtot=$((tot2 - tot1))
  didl=$((idl2 - idl1))
  [[ $dtot -eq 0 ]] && dtot=1
  cpu_pct=$(( 100 - (didl * 100 / dtot) ))

  # Get previous rate
  local cur_rate
  cur_rate=$(shaper_state_get rate_applied)
  [[ "$cur_rate" =~ ^[0-9]+$ ]] || cur_rate=$SHAPER_MAX_RATE_MBIT
  
  local tg_ts
  tg_ts=$(shaper_state_get telegram_last_ts)
  [[ "$tg_ts" =~ ^[0-9]+$ ]] || tg_ts=0

  local next_rate=$cur_rate
  local op="hold"

  # Determine bounds
  local max_r=$SHAPER_MAX_RATE_MBIT
  local min_r=$SHAPER_MIN_RATE_MBIT

  if [[ $cpu_pct -ge $SHAPER_CPU_TARGET_PCT ]]; then
    local decr=$(( cur_rate * SHAPER_STEP_DOWN_PCT / 100 ))
    [[ $decr -lt 1 ]] && decr=1
    next_rate=$(( cur_rate - decr ))
    [[ $next_rate -lt $min_r ]] && next_rate=$min_r
  elif [[ $cpu_pct -lt $((SHAPER_CPU_TARGET_PCT - 15)) ]]; then
    local incr=$(( cur_rate * SHAPER_STEP_UP_PCT / 100 ))
    [[ $incr -lt 1 ]] && incr=1
    next_rate=$(( cur_rate + incr ))
    [[ $next_rate -gt $max_r ]] && next_rate=$max_r
  fi
  
  if [[ "$next_rate" -lt "$cur_rate" ]]; then
    op="step_down"
  elif [[ "$next_rate" -gt "$cur_rate" ]]; then
    op="step_up"
  else
    op="hold"
  fi

  # Apply tc using next limit
  if [[ "$op" != "hold" ]]; then
    tc_apply "$SHAPER_IFACE" "$next_rate" "$SHAPER_VPN_PORTS"
  fi
  
  # Telegram Notification
  if [[ "$op" != "hold" && "$SHAPER_TELEGRAM_ALERTS" == "1" ]]; then
     local cd=${SHAPER_TELEGRAM_COOLDOWN_SEC:-60}
     if ((ts - tg_ts >= cd)); then
        local host
        host=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "unknown")
        local emoji="⚠️"
        [[ "$op" == "step_up" ]] && emoji="✅"
        
        local txt="${emoji} CPU-Aware Shaper on ${host}
Operation: ${op}
CPU Load: ${cpu_pct}% (Target: ${SHAPER_CPU_TARGET_PCT}%)
Bandwidth: ${cur_rate} Mbit/s ➔ ${next_rate} Mbit/s
Limits: MIN=${min_r}M, MAX=${max_r}M
Interface: ${SHAPER_IFACE}"

        send_shaper_telegram "$txt" &
        tg_ts=$ts
     fi
  fi
  
  # Record State
  shaper_state_write_kv "rate_applied=${next_rate}\ncpu_pct=${cpu_pct}\nts=${ts}\ntelegram_last_ts=${tg_ts}"
  
  # Write Status
  local one_line="cpu_shaper: cpu=${cpu_pct}% target=${SHAPER_CPU_TARGET_PCT}% op=${op} old=${cur_rate}M new=${next_rate}M"
  {
    echo "ts=${ts}"
    echo "iface=${SHAPER_IFACE}"
    echo "cpu_pct=${cpu_pct}"
    echo "rate_applied_mbit=${next_rate}"
    echo "tc_op=${op}"
    echo "one_line=${one_line}"
  } >"${SHAPER_STATUS_FILE}.tmp"
  mkdir -p "$(dirname "$SHAPER_STATUS_FILE")" 2>/dev/null || true
  mv "${SHAPER_STATUS_FILE}.tmp" "$SHAPER_STATUS_FILE"

  echo "$one_line"
}

main "$@"
