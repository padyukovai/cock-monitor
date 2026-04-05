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
  printf '\nstats alerts: ALERT_ON_STATS=%s\n' "$ALERT_ON_STATS"
  printf 'STATS_DROP_MIN=%s STATS_INSERT_FAILED_MIN=%s STATS_COOLDOWN_SECONDS=%s\n' \
    "$STATS_DROP_MIN" "$STATS_INSERT_FAILED_MIN" "$STATS_COOLDOWN_SECONDS"
  if command -v conntrack >/dev/null 2>&1; then
    local ds ifs
    ds=$(sum_conntrack_stat drop)
    ifs=$(sum_conntrack_stat insert_failed)
    printf 'current sums: drop=%s insert_failed=%s\n' "$ds" "$ifs"
  fi
}
