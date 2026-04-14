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
  # cock-status / Telegram /status: max lines from `ip -s link show dev <iface>` (RX/TX drops/errors).
  STATUS_IP_LINK_HEAD_LINES="${STATUS_IP_LINK_HEAD_LINES:-22}"
  # check-conntrack METRICS_DB: collect first line of `tc qdisc show dev <iface> root` (0 = skip).
  METRICS_COLLECT_TC_QDISC="${METRICS_COLLECT_TC_QDISC:-1}"
  # STATS Telegram: treat SHAPER_STATUS_FILE as fresh if ts= within this many minutes (0 = ignore mtime of ts).
  STATS_ALERT_SHAPER_MAX_AGE_MIN="${STATS_ALERT_SHAPER_MAX_AGE_MIN:-15}"
}

# Read /proc/meminfo field value in kB (second column). Prints nothing if missing.
_meminfo_kb() {
  local key=$1
  [[ -r /proc/meminfo ]] || return 0
  awk -v k="$key" '$1 == k { print $2; exit }' /proc/meminfo 2>/dev/null || true
}

# sockstat line label is e.g. "TCP:" or "TCP6:"; key is e.g. inuse, orphan, tw. Prints value or nothing.
_sockstat_field() {
  local label=$1 key=$2
  [[ -r /proc/net/sockstat ]] || return 0
  awk -v lbl="$label" -v want="$key" '
    $1 == lbl {
      for (i = 2; i < NF; i += 2)
        if ($(i) == want) {
          print $(i + 1)
          exit
        }
    }' /proc/net/sockstat 2>/dev/null || true
}

# Fills METRICS_DB_* for host_samples row (check-conntrack.sh). Read-only /proc and optional tc/shaper file.
metrics_collect_host_for_db() {
  METRICS_DB_LOAD1=""
  METRICS_DB_MEM_AVAIL_KB=""
  METRICS_DB_SWAP_USED_KB=""
  METRICS_DB_TCP_INUSE=""
  METRICS_DB_TCP_ORPHAN=""
  METRICS_DB_TCP_TW=""
  METRICS_DB_TCP6_INUSE=""
  METRICS_DB_SHAPER_RATE_MBIT=""
  METRICS_DB_SHAPER_CPU_PCT=""
  METRICS_DB_TC_QDISC_ROOT=""

  if [[ -r /proc/loadavg ]]; then
    METRICS_DB_LOAD1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || true)
  fi

  local ma st sf v
  ma=$(_meminfo_kb 'MemAvailable:')
  st=$(_meminfo_kb 'SwapTotal:')
  sf=$(_meminfo_kb 'SwapFree:')
  [[ -n "$ma" && "$ma" =~ ^[0-9]+$ ]] && METRICS_DB_MEM_AVAIL_KB=$ma
  if [[ -n "$st" && "$st" =~ ^[0-9]+$ && -n "$sf" && "$sf" =~ ^[0-9]+$ ]]; then
    METRICS_DB_SWAP_USED_KB=$((st - sf))
  fi

  v=$(_sockstat_field 'TCP:' inuse)
  [[ -n "$v" && "$v" =~ ^[0-9]+$ ]] && METRICS_DB_TCP_INUSE=$v
  v=$(_sockstat_field 'TCP:' orphan)
  [[ -n "$v" && "$v" =~ ^[0-9]+$ ]] && METRICS_DB_TCP_ORPHAN=$v
  v=$(_sockstat_field 'TCP:' tw)
  [[ -n "$v" && "$v" =~ ^[0-9]+$ ]] && METRICS_DB_TCP_TW=$v
  v=$(_sockstat_field 'TCP6:' inuse)
  [[ -n "$v" && "$v" =~ ^[0-9]+$ ]] && METRICS_DB_TCP6_INUSE=$v

  local shf="${SHAPER_STATUS_FILE:-/var/lib/cock-monitor/cpu_shaper.status}"
  if [[ -f "$shf" ]]; then
    local _k _v
    while IFS='=' read -r _k _v || [[ -n "$_k" ]]; do
      case "$_k" in
        rate_applied_mbit) METRICS_DB_SHAPER_RATE_MBIT="${_v//$'\r'/}" ;;
        cpu_pct) METRICS_DB_SHAPER_CPU_PCT="${_v//$'\r'/}" ;;
      esac
    done <"$shf"
  fi

  if [[ "${METRICS_COLLECT_TC_QDISC:-1}" == "1" ]] && command -v tc >/dev/null 2>&1; then
    local ifc="${SHAPER_IFACE:-ens3}"
    v=$(tc qdisc show dev "$ifc" root 2>/dev/null | head -n1 | tr -d '\r' || true)
    if [[ -n "$v" ]]; then
      if [[ "${#v}" -gt 400 ]]; then
        METRICS_DB_TC_QDISC_ROOT="${v:0:400}"
      else
        METRICS_DB_TC_QDISC_ROOT=$v
      fi
    fi
  fi
}

# Short host context for STATS Telegram alerts (load, mem/swap, TCP line, shaper). Prints 3–5 lines to stdout.
format_stats_alert_host_context() {
  local load1="" ma="" st="" sf="" tcp_line=""
  if [[ -r /proc/loadavg ]]; then
    load1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || true)
  fi
  ma=$(_meminfo_kb 'MemAvailable:')
  st=$(_meminfo_kb 'SwapTotal:')
  sf=$(_meminfo_kb 'SwapFree:')
  printf 'load1=%s' "${load1:-n/a}"
  if [[ -n "$ma" && "$ma" =~ ^[0-9]+$ ]]; then
    printf ' MemAvailable=%s kB' "$ma"
  else
    printf ' MemAvailable=n/a'
  fi
  if [[ -n "$st" && "$st" =~ ^[0-9]+$ && -n "$sf" && "$sf" =~ ^[0-9]+$ ]]; then
    printf ' swap_used=%s/%s kB' "$((st - sf))" "$st"
  else
    printf ' swap=n/a'
  fi
  printf '\n'
  if [[ -r /proc/net/sockstat ]]; then
    tcp_line=$(grep -m1 '^TCP:' /proc/net/sockstat 2>/dev/null | tr -d '\r' || true)
    if [[ -n "$tcp_line" ]]; then
      [[ "${#tcp_line}" -gt 220 ]] && tcp_line="${tcp_line:0:220}..."
      printf '%s\n' "$tcp_line"
    else
      printf 'sockstat TCP: (no line)\n'
    fi
  else
    printf 'sockstat TCP: n/a\n'
  fi

  local shf="${SHAPER_STATUS_FILE:-/var/lib/cock-monitor/cpu_shaper.status}"
  local max_min="${STATS_ALERT_SHAPER_MAX_AGE_MIN:-15}"
  [[ "$max_min" =~ ^[0-9]+$ ]] || max_min=15
  local fts="" fr="" fcpu="" k v
  if [[ -f "$shf" ]]; then
    while IFS='=' read -r k v || [[ -n "$k" ]]; do
      case "$k" in
        ts) fts="${v//$'\r'/}" ;;
        rate_applied_mbit) fr="${v//$'\r'/}" ;;
        cpu_pct) fcpu="${v//$'\r'/}" ;;
      esac
    done <"$shf"
  fi
  local now age lim fresh=0
  now=$(date +%s 2>/dev/null) || now=0
  if [[ -f "$shf" ]] && [[ -n "$fr" || -n "$fcpu" ]]; then
    if [[ "$max_min" -eq 0 ]]; then
      fresh=1
    elif [[ "$fts" =~ ^[0-9]+$ ]]; then
      age=$((now - fts))
      lim=$((max_min * 60))
      if [[ "$age" -le "$lim" ]]; then
        fresh=1
      fi
    fi
  fi
  if [[ "$fresh" -eq 1 ]]; then
    printf 'shaper: %s Mbit/s cpu=%s%%\n' "${fr:-?}" "${fcpu:-?}"
  else
    printf 'shaper: no data\n'
  fi
}

# Effective WAN iface for status: STATUS_WAN_IFACE, else SHAPER_IFACE, else ens3.
_status_wan_iface() {
  if [[ -n "${STATUS_WAN_IFACE:-}" ]]; then
    printf '%s' "$STATUS_WAN_IFACE"
  elif [[ -n "${SHAPER_IFACE:-}" ]]; then
    printf '%s' "$SHAPER_IFACE"
  else
    printf '%s' 'ens3'
  fi
}

# Host RAM/swap, loadavg, TCP sockstat, optional ip -s link, optional systemd units (compact, read-only).
_append_host_snapshot_to_status() {
  printf '\n--- Host snapshot ---\n'

  local ma st sf
  ma=$(_meminfo_kb 'MemAvailable:')
  st=$(_meminfo_kb 'SwapTotal:')
  sf=$(_meminfo_kb 'SwapFree:')
  if [[ -r /proc/meminfo ]]; then
    printf 'mem:'
    if [[ -n "$ma" && "$ma" =~ ^[0-9]+$ ]]; then
      printf ' MemAvailable=%s kB' "$ma"
    else
      printf ' MemAvailable=(n/a)'
    fi
    if [[ -n "$st" && "$st" =~ ^[0-9]+$ && -n "$sf" && "$sf" =~ ^[0-9]+$ ]]; then
      printf ' | swap used=%s/%s kB (free %s kB)' "$((st - sf))" "$st" "$sf"
    elif [[ -n "$st" || -n "$sf" ]]; then
      printf ' | swap SwapTotal=%s kB SwapFree=%s kB' "${st:-?}" "${sf:-?}"
    fi
    printf '\n'
  else
    printf 'mem: (/proc/meminfo not readable)\n'
  fi

  if [[ -r /proc/loadavg ]]; then
    printf 'loadavg: %s\n' "$(tr -d '\r' </proc/loadavg)"
  else
    printf 'loadavg: (/proc/loadavg not readable)\n'
  fi

  if [[ -r /proc/net/sockstat ]]; then
    printf 'sockstat:\n'
    grep -E '^(TCP|TCP6):' /proc/net/sockstat 2>/dev/null | head -n 4 || printf '(no TCP lines)\n'
  else
    printf 'sockstat: (/proc/net/sockstat not readable)\n'
  fi

  local wan_iface ip_lines
  wan_iface=$(_status_wan_iface)
  ip_lines="${STATUS_IP_LINK_HEAD_LINES:-22}"
  [[ "$ip_lines" =~ ^[0-9]+$ ]] || ip_lines=22
  [[ "$ip_lines" -gt 60 ]] && ip_lines=60
  [[ "$ip_lines" -lt 8 ]] && ip_lines=8
  printf '\nWAN iface %s (ip -s link, first %s lines):\n' "$wan_iface" "$ip_lines"
  if command -v ip >/dev/null 2>&1; then
    if ip link show dev "$wan_iface" >/dev/null 2>&1; then
      ip -s link show dev "$wan_iface" 2>/dev/null | head -n "$ip_lines" || printf '(ip -s link failed)\n'
    else
      printf '(interface not found)\n'
    fi
  else
    printf '(ip command not found)\n'
  fi

  if [[ -n "${STATUS_EXTRA_UNITS:-}" ]]; then
    printf '\nextra units (STATUS_EXTRA_UNITS):\n'
    local u act ts
    for u in ${STATUS_EXTRA_UNITS}; do
      [[ -z "$u" ]] && continue
      if command -v systemctl >/dev/null 2>&1; then
        act=$(systemctl is-active "$u" 2>/dev/null || printf '%s' '?')
        ts=$(systemctl show "$u" -p ActiveEnterTimestamp --value 2>/dev/null || true)
        [[ -z "$ts" ]] && ts='?'
        printf '  %s: %s | ActiveEnter=%s\n' "$u" "$act" "$ts"
      else
        printf '  %s: (systemctl not found)\n' "$u"
      fi
    done
  fi
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
  local now_msk; now_msk=$(TZ='Europe/Moscow' date +'%Y-%m-%d %H:%M:%S MSK')
  printf 'time: %s\nhost: %s\n' "$now_msk" "$host"
  _append_host_snapshot_to_status
  printf '\n'
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
