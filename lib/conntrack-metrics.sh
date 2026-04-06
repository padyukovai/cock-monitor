# Shared nf_conntrack metrics helpers (sourced by check-conntrack.sh and cock-status.sh).
# shellcheck shell=bash
[[ -n "${_COCK_MONITOR_CONNTRACK_METRICS_SH:-}" ]] && return
_COCK_MONITOR_CONNTRACK_METRICS_SH=1

apply_defaults() {
  WARN_PERCENT="${WARN_PERCENT:-80}"
  CRIT_PERCENT="${CRIT_PERCENT:-95}"
  COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-3600}"
  STATE_FILE="${STATE_FILE:-/var/lib/cock-monitor/state}"
  CHECK_CONNTRACK_FILL="${CHECK_CONNTRACK_FILL:-1}"
  INCLUDE_CONNTRACK_STATS_LINE="${INCLUDE_CONNTRACK_STATS_LINE:-1}"
  DRY_RUN="${DRY_RUN:-0}"
  ALERT_ON_STATS="${ALERT_ON_STATS:-0}"
  STATS_DROP_MIN="${STATS_DROP_MIN:-0}"
  STATS_INSERT_FAILED_MIN="${STATS_INSERT_FAILED_MIN:-0}"
  STATS_COOLDOWN_SECONDS="${STATS_COOLDOWN_SECONDS:-$COOLDOWN_SECONDS}"
  # SQLite metrics history (check-conntrack.sh)
  METRICS_DB="${METRICS_DB:-/var/lib/cock-monitor/metrics.db}"
  METRICS_RECORD_EVERY_RUN="${METRICS_RECORD_EVERY_RUN:-1}"
  METRICS_RECORD_MIN_INTERVAL_SEC="${METRICS_RECORD_MIN_INTERVAL_SEC:-0}"
  METRICS_RETENTION_DAYS="${METRICS_RETENTION_DAYS:-14}"
  METRICS_MAX_ROWS="${METRICS_MAX_ROWS:-0}"
  # Delta/rate stats alerts (requires conntrack + at least one prior DB row)
  ALERT_ON_STATS_DELTA="${ALERT_ON_STATS_DELTA:-0}"
  STATS_DELTA_MIN_INTERVAL_SEC="${STATS_DELTA_MIN_INTERVAL_SEC:-60}"
  STATS_DELTA_DROP_MIN="${STATS_DELTA_DROP_MIN:-0}"
  STATS_DELTA_INSERT_FAILED_MIN="${STATS_DELTA_INSERT_FAILED_MIN:-0}"
  STATS_DELTA_EARLY_DROP_MIN="${STATS_DELTA_EARLY_DROP_MIN:-0}"
  STATS_DELTA_ERROR_MIN="${STATS_DELTA_ERROR_MIN:-0}"
  STATS_DELTA_INVALID_MIN="${STATS_DELTA_INVALID_MIN:-0}"
  STATS_DELTA_SEARCH_RESTART_MIN="${STATS_DELTA_SEARCH_RESTART_MIN:-0}"
  STATS_RATE_DROP_PER_MIN="${STATS_RATE_DROP_PER_MIN:-0}"
  STATS_RATE_INSERT_FAILED_PER_MIN="${STATS_RATE_INSERT_FAILED_PER_MIN:-0}"
  STATS_RATE_EARLY_DROP_PER_MIN="${STATS_RATE_EARLY_DROP_PER_MIN:-0}"
  STATS_RATE_ERROR_PER_MIN="${STATS_RATE_ERROR_PER_MIN:-0}"
  STATS_RATE_INVALID_PER_MIN="${STATS_RATE_INVALID_PER_MIN:-0}"
  STATS_RATE_SEARCH_RESTART_PER_MIN="${STATS_RATE_SEARCH_RESTART_PER_MIN:-0}"
  # Load average alert
  LA_ALERT_ENABLE="${LA_ALERT_ENABLE:-0}"
  LA_WARN_THRESHOLD="${LA_WARN_THRESHOLD:-1.5}"
  LA_ALERT_COOLDOWN_SEC="${LA_ALERT_COOLDOWN_SEC:-600}"
}

# Unsigned 32-bit counter delta (conntrack stats may wrap).
# Prints decimal delta or empty string if inputs are not non-negative integers.
u32_counter_delta() {
  local old=$1 new=$2
  [[ "$old" =~ ^[0-9]+$ && "$new" =~ ^[0-9]+$ ]] || {
    printf ''
    return 0
  }
  if ((new >= old)); then
    printf '%s' "$((new - old))"
  else
    printf '%s' "$((4294967296 - old + new))"
  fi
  return 0
}

sum_conntrack_stat() {
  local name=$1
  local sum=0 tok v
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    for tok in $line; do
      case "$tok" in
        "${name}="*)
          v="${tok#*=}"
          [[ "$v" =~ ^[0-9]+$ ]] && sum=$((sum + v))
          ;;
      esac
    done
  done < <(conntrack -S 2>/dev/null || true)
  printf '%s' "$sum"
}

conntrack_stats_line() {
  conntrack -S 2>/dev/null | head -n1 | tr -d '\r' || true
}

# Sets FILL_COUNT, FILL_MAX, FILL_PCT, FILL_SEVERITY (0=ok, 1=warn, 2=crit). Returns 1 on read/config error.
compute_fill_severity() {
  local count max pct
  local cf cm
  cf="/proc/sys/net/netfilter/nf_conntrack_count"
  cm="/proc/sys/net/netfilter/nf_conntrack_max"
  [[ -r "$cf" && -r "$cm" ]] || {
    echo "check-conntrack: cannot read $cf or $cm (conntrack module enabled?)" >&2
    return 1
  }
  count=$(tr -d '[:space:]' <"$cf" || true)
  max=$(tr -d '[:space:]' <"$cm" || true)
  [[ "$count" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ ]] || {
    echo "check-conntrack: unexpected values count='$count' max='$max'" >&2
    return 1
  }
  if [[ "$max" -eq 0 ]]; then
    echo "check-conntrack: nf_conntrack_max is 0 (conntrack disabled?)" >&2
    return 1
  fi
  pct=$((100 * count / max))
  FILL_COUNT=$count
  FILL_MAX=$max
  FILL_PCT=$pct
  if [[ "$pct" -ge "$CRIT_PERCENT" ]]; then
    FILL_SEVERITY=2
    return 0
  fi
  if [[ "$pct" -ge "$WARN_PERCENT" ]]; then
    FILL_SEVERITY=1
    return 0
  fi
  FILL_SEVERITY=0
  return 0
}

# Prints human-readable status to stdout. When CHECK_CONNTRACK_FILL=1, caller must run compute_fill_severity first.
format_full_status_text() {
  local host
  host=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo unknown)
  printf 'host: %s\n\n' "$host"
  if [[ "$CHECK_CONNTRACK_FILL" == "1" ]]; then
    printf 'conntrack fill: %s%% (%s/%s)\n' "$FILL_PCT" "$FILL_COUNT" "$FILL_MAX"
    case "$FILL_SEVERITY" in
      0) printf 'level: OK (warn>=%s%% crit>=%s%%)\n' "$WARN_PERCENT" "$CRIT_PERCENT" ;;
      1) printf 'level: WARNING (warn>=%s%% crit>=%s%%)\n' "$WARN_PERCENT" "$CRIT_PERCENT" ;;
      2) printf 'level: CRITICAL (warn>=%s%% crit>=%s%%)\n' "$WARN_PERCENT" "$CRIT_PERCENT" ;;
      *) printf 'level: unknown\n' ;;
    esac
  else
    printf 'conntrack fill check: disabled (CHECK_CONNTRACK_FILL=0)\n'
  fi
  printf '\nINCLUDE_CONNTRACK_STATS_LINE=%s\n' "$INCLUDE_CONNTRACK_STATS_LINE"
  if command -v conntrack >/dev/null 2>&1; then
    printf '\nconntrack -S:\n'
    conntrack -S 2>/dev/null || printf '(conntrack -S failed)\n'
  else
    printf '\nconntrack: command not found\n'
  fi
  printf '\nstats alerts: ALERT_ON_STATS=%s ALERT_ON_STATS_DELTA=%s\n' \
    "$ALERT_ON_STATS" "$ALERT_ON_STATS_DELTA"
  printf 'STATS_DROP_MIN=%s STATS_INSERT_FAILED_MIN=%s STATS_COOLDOWN_SECONDS=%s\n' \
    "$STATS_DROP_MIN" "$STATS_INSERT_FAILED_MIN" "$STATS_COOLDOWN_SECONDS"
  printf 'METRICS_DB=%s METRICS_RECORD_EVERY_RUN=%s METRICS_RETENTION_DAYS=%s\n' \
    "$METRICS_DB" "$METRICS_RECORD_EVERY_RUN" "$METRICS_RETENTION_DAYS"
  if command -v conntrack >/dev/null 2>&1; then
    local ds ifs ed er inv sr
    ds=$(sum_conntrack_stat drop)
    ifs=$(sum_conntrack_stat insert_failed)
    ed=$(sum_conntrack_stat early_drop)
    er=$(sum_conntrack_stat error)
    inv=$(sum_conntrack_stat invalid)
    sr=$(sum_conntrack_stat search_restart)
    printf 'current sums: drop=%s insert_failed=%s early_drop=%s error=%s invalid=%s search_restart=%s\n' \
      "$ds" "$ifs" "$ed" "$er" "$inv" "$sr"
  fi
  
  local shaper_file="${SHAPER_STATUS_FILE:-/var/lib/cock-monitor/cpu_shaper.status}"
  printf '\n--- VPN CPU Shaper ---\n'
  if [[ -f "$shaper_file" ]]; then
    local s_ts="" s_iface="" s_cpu="" s_rate="" s_op=""
    while IFS='=' read -r k v; do
      case "$k" in
        ts) s_ts="$v" ;;
        iface) s_iface="$v" ;;
        cpu_pct) s_cpu="$v" ;;
        rate_applied_mbit) s_rate="$v" ;;
        tc_op) s_op="$v" ;;
      esac
    done < "$shaper_file"
    
    local emoji="🟢"
    local op_rus="стабильно (hold)"
    if [[ "$s_op" == "step_down" ]]; then
      emoji="🔴"
      op_rus="ограничение (step_down)"
    elif [[ "$s_op" == "step_up" ]]; then
      emoji="🟡"
      op_rus="ускорение (step_up)"
    elif [[ "$s_cpu" -gt 80 ]]; then
      emoji="🟠"
    fi
    
    printf "%s Скорость VPN: %s Mbit/s (на %s)\n" "$emoji" "${s_rate:-?}" "${s_iface:-?}"
    printf "   Действие: %s\n" "$op_rus"
    printf "   Загрузка CPU: %s%%\n" "${s_cpu:-?}"
    
    if [[ -n "$s_ts" ]]; then
      local now; now=$(date +%s)
      local diff=$((now - s_ts))
      if (( diff > 300 )); then
        printf "   ⚠️ Данные устарели на %d сек.\n" "$diff"
      fi
    fi
  else
    printf "Отключен или нет данных\n"
  fi
}
