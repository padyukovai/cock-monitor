# Shared incident-sampler helpers (sourced by incident-sampler.sh).
# shellcheck shell=bash
[[ -n "${_COCK_MONITOR_INCIDENT_METRICS_SH:-}" ]] && return
_COCK_MONITOR_INCIDENT_METRICS_SH=1

incident_apply_defaults() {
  INCIDENT_SAMPLER_ENABLE="${INCIDENT_SAMPLER_ENABLE:-0}"
  INCIDENT_LOG_DIR="${INCIDENT_LOG_DIR:-/var/lib/cock-monitor}"
  INCIDENT_STATE_FILE="${INCIDENT_STATE_FILE:-/var/lib/cock-monitor/incident_sampler.state}"

  INCIDENT_PING_TARGETS="${INCIDENT_PING_TARGETS:-1.1.1.1 8.8.8.8}"
  INCIDENT_PING_COUNT="${INCIDENT_PING_COUNT:-2}"
  INCIDENT_PING_TIMEOUT_SEC="${INCIDENT_PING_TIMEOUT_SEC:-1}"
  INCIDENT_PING_LOSS_WARN_PCT="${INCIDENT_PING_LOSS_WARN_PCT:-20}"

  INCIDENT_DNS_HOST="${INCIDENT_DNS_HOST:-api.telegram.org}"
  INCIDENT_DNS_TIMEOUT_SEC="${INCIDENT_DNS_TIMEOUT_SEC:-2}"
  INCIDENT_DNS_FAIL_STREAK_WARN="${INCIDENT_DNS_FAIL_STREAK_WARN:-3}"

  INCIDENT_CONNTRACK_WARN_PCT="${INCIDENT_CONNTRACK_WARN_PCT:-85}"
  INCIDENT_CONNTRACK_CRIT_PCT="${INCIDENT_CONNTRACK_CRIT_PCT:-95}"

  INCIDENT_SYSTEMD_UNITS="${INCIDENT_SYSTEMD_UNITS:-x-ui.service}"

  INCIDENT_ALERT_ENABLE="${INCIDENT_ALERT_ENABLE:-0}"
  INCIDENT_ALERT_COOLDOWN_SEC="${INCIDENT_ALERT_COOLDOWN_SEC:-300}"
  # After recovery (WARN/CRIT -> OK), send HTML post-mortem from JSONL window (needs python3).
  INCIDENT_POSTMORTEM_ENABLE="${INCIDENT_POSTMORTEM_ENABLE:-1}"
  DRY_RUN="${DRY_RUN:-0}"
}

incident_now_epoch() {
  date +%s
}

incident_now_iso_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

incident_hostname() {
  hostname -f 2>/dev/null || hostname 2>/dev/null || echo "unknown-host"
}

incident_json_escape() {
  local s=${1:-}
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

incident_json_quote() {
  printf '"%s"' "$(incident_json_escape "${1:-}")"
}

incident_is_int() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

incident_is_num() {
  [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

incident_safe_pct() {
  local n=${1:-0} d=${2:-0}
  if ! incident_is_int "$n" || ! incident_is_int "$d" || (( d <= 0 )); then
    printf '0'
    return
  fi
  printf '%d' $((n * 100 / d))
}

incident_collect_conntrack() {
  INCIDENT_CONNTRACK_COUNT=0
  INCIDENT_CONNTRACK_MAX=0
  INCIDENT_CONNTRACK_FILL_PCT=0
  local count max
  count=$(sysctl -n net.netfilter.nf_conntrack_count 2>/dev/null || true)
  max=$(sysctl -n net.netfilter.nf_conntrack_max 2>/dev/null || true)
  incident_is_int "$count" && INCIDENT_CONNTRACK_COUNT="$count"
  incident_is_int "$max" && INCIDENT_CONNTRACK_MAX="$max"
  INCIDENT_CONNTRACK_FILL_PCT=$(incident_safe_pct "$INCIDENT_CONNTRACK_COUNT" "$INCIDENT_CONNTRACK_MAX")
}

incident_collect_dns() {
  INCIDENT_DNS_OK=0
  INCIDENT_DNS_LATENCY_MS=0
  INCIDENT_DNS_ERROR=""
  local host ts_start ts_end rc
  host=${INCIDENT_DNS_HOST}
  ts_start=$(date +%s%3N 2>/dev/null || true)
  if timeout "${INCIDENT_DNS_TIMEOUT_SEC}s" getent ahostsv4 "$host" >/dev/null 2>&1; then
    INCIDENT_DNS_OK=1
  else
    rc=$?
    INCIDENT_DNS_ERROR="lookup_failed_rc_${rc}"
  fi
  ts_end=$(date +%s%3N 2>/dev/null || true)
  if incident_is_int "$ts_start" && incident_is_int "$ts_end" && (( ts_end >= ts_start )); then
    INCIDENT_DNS_LATENCY_MS=$((ts_end - ts_start))
  fi
}

incident_collect_ping_one() {
  local target=$1
  local out tx rx loss avg
  local cnt="${INCIDENT_PING_COUNT}"
  local timeout_sec="${INCIDENT_PING_TIMEOUT_SEC}"
  # Force C locale so summary lines match the parser (some hosts use translated ping output).
  out=$(LANG=C LC_ALL=C ping -n -c "$cnt" -W "$timeout_sec" "$target" 2>&1 || true)
  tx=$(printf '%s\n' "$out" | awk -F'[ ,]+' '/packets transmitted/ {print $1; exit}')
  rx=$(printf '%s\n' "$out" | awk -F'[ ,]+' '/packets transmitted/ {print $4; exit}')
  # "0% packet loss" — do not use fixed $N (field layout differs by locale/version).
  loss=$(printf '%s\n' "$out" | sed -n 's/.* \([0-9][0-9]*\)% packet loss.*/\1/p' | head -n1)
  avg=$(printf '%s\n' "$out" | awk -F'=' '/min\/avg\/max/ {gsub(/ /, "", $2); split($2,a,"/"); print a[2]; exit}')

  incident_is_int "$tx" || tx=0
  incident_is_int "$rx" || rx=0
  incident_is_int "$loss" || loss=100
  incident_is_num "$avg" || avg=0
  printf '%s|%s|%s|%s' "$tx" "$rx" "$loss" "$avg"
}

incident_collect_ping() {
  INCIDENT_PING_JSON="[]"
  INCIDENT_PING_MAX_LOSS=0
  local first=1 target tuple tx rx loss avg
  local json="["
  for target in $INCIDENT_PING_TARGETS; do
    tuple=$(incident_collect_ping_one "$target")
    IFS='|' read -r tx rx loss avg <<<"$tuple"
    (( loss > INCIDENT_PING_MAX_LOSS )) && INCIDENT_PING_MAX_LOSS=$loss
    (( first == 0 )) && json+=","
    first=0
    json+="{\"target\":$(incident_json_quote "$target"),\"tx\":${tx},\"rx\":${rx},\"loss_pct\":${loss},\"avg_ms\":${avg}}"
  done
  json+="]"
  INCIDENT_PING_JSON="$json"
}

incident_collect_ss() {
  INCIDENT_TCP_ESTAB=0
  INCIDENT_TCP_SYN_RECV=0
  INCIDENT_TCP_TIME_WAIT=0
  local states
  states=$(ss -tan 2>/dev/null | awk 'NR>1 { c[$1]++ } END { for (k in c) print k, c[k] }' || true)
  INCIDENT_TCP_ESTAB=$(printf '%s\n' "$states" | awk '$1=="ESTAB"{print $2; exit}')
  INCIDENT_TCP_SYN_RECV=$(printf '%s\n' "$states" | awk '$1=="SYN-RECV"{print $2; exit}')
  INCIDENT_TCP_TIME_WAIT=$(printf '%s\n' "$states" | awk '$1=="TIME-WAIT"{print $2; exit}')
  incident_is_int "$INCIDENT_TCP_ESTAB" || INCIDENT_TCP_ESTAB=0
  incident_is_int "$INCIDENT_TCP_SYN_RECV" || INCIDENT_TCP_SYN_RECV=0
  incident_is_int "$INCIDENT_TCP_TIME_WAIT" || INCIDENT_TCP_TIME_WAIT=0
}

incident_collect_load_mem() {
  INCIDENT_LOAD1=0
  INCIDENT_MEM_AVAIL_KB=0
  local l1 ma
  l1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || true)
  ma=$(awk '$1 == "MemAvailable:" { print $2; exit }' /proc/meminfo 2>/dev/null || true)
  incident_is_num "$l1" && INCIDENT_LOAD1="$l1"
  incident_is_int "$ma" && INCIDENT_MEM_AVAIL_KB="$ma"
}

incident_collect_units() {
  INCIDENT_UNITS_JSON="{}"
  local unit status first=1
  local json="{"
  for unit in $INCIDENT_SYSTEMD_UNITS; do
    status=$(systemctl is-active "$unit" 2>/dev/null || true)
    [[ -n "$status" ]] || status="unknown"
    (( first == 0 )) && json+=","
    first=0
    json+="$(incident_json_quote "$unit"):$(incident_json_quote "$status")"
  done
  json+="}"
  INCIDENT_UNITS_JSON="$json"
}
