#!/usr/bin/env bash
# Lightweight nf_conntrack fill check + optional conntrack -S stats; Telegram alerts with cooldown.
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/conntrack-metrics.sh
source "${_COCK_REPO_ROOT}/lib/conntrack-metrics.sh"

_cock_conntrack_decide() {
  PYTHONPATH="${_COCK_REPO_ROOT}" python3 -m cock_monitor conntrack-decide "$@"
}

# JSON number or null (for conntrack-decide stdin).
_cock_json_uint_or_null() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] && printf '%s' "$1" || printf 'null'
}

_cock_json_bool() {
  [[ "$1" == "1" ]] && printf 'true' || printf 'false'
}

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
    printf 'la_last_ts=%s\n' "${la_last_ts}"
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

metrics_wanted() {
  [[ "$METRICS_RECORD_EVERY_RUN" == "1" || "$ALERT_ON_STATS_DELTA" == "1" ]]
}

metrics_init_db() {
  sqlite3 "$METRICS_DB" <<'SQL'
CREATE TABLE IF NOT EXISTS conntrack_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  fill_pct INTEGER,
  fill_count INTEGER,
  fill_max INTEGER,
  "drop" INTEGER NOT NULL DEFAULT 0,
  insert_failed INTEGER NOT NULL DEFAULT 0,
  early_drop INTEGER NOT NULL DEFAULT 0,
  "error" INTEGER NOT NULL DEFAULT 0,
  invalid INTEGER NOT NULL DEFAULT 0,
  search_restart INTEGER NOT NULL DEFAULT 0,
  interval_sec INTEGER,
  delta_drop INTEGER,
  delta_insert_failed INTEGER,
  delta_early_drop INTEGER,
  delta_error INTEGER,
  delta_invalid INTEGER,
  delta_search_restart INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON conntrack_samples(ts);
CREATE TABLE IF NOT EXISTS host_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  load1 REAL,
  mem_avail_kb INTEGER,
  swap_used_kb INTEGER,
  tcp_inuse INTEGER,
  tcp_orphan INTEGER,
  tcp_tw INTEGER,
  tcp6_inuse INTEGER,
  shaper_rate_mbit REAL,
  shaper_cpu_pct INTEGER,
  tc_qdisc_root TEXT
);
CREATE INDEX IF NOT EXISTS idx_host_samples_ts ON host_samples(ts);
SQL
}

metrics_read_last() {
  sqlite3 -separator '|' "$METRICS_DB" \
    "SELECT ts, \"drop\", insert_failed, early_drop, \"error\", invalid, search_restart FROM conntrack_samples ORDER BY id DESC LIMIT 1;" 2>/dev/null || true
}

metrics_retention() {
  [[ "$METRICS_RETENTION_DAYS" =~ ^[0-9]+$ && "$METRICS_RETENTION_DAYS" -gt 0 ]] || return 0
  local cutoff now
  now=$(now_epoch)
  cutoff=$((now - METRICS_RETENTION_DAYS * 86400))
  sqlite3 "$METRICS_DB" "DELETE FROM conntrack_samples WHERE ts < ${cutoff}; DELETE FROM host_samples WHERE ts < ${cutoff};" 2>/dev/null || true
}

metrics_trim_max_rows() {
  [[ "$METRICS_MAX_ROWS" =~ ^[0-9]+$ && "$METRICS_MAX_ROWS" -gt 0 ]] || return 0
  sqlite3 "$METRICS_DB" "DELETE FROM conntrack_samples WHERE id NOT IN (SELECT id FROM conntrack_samples ORDER BY id DESC LIMIT ${METRICS_MAX_ROWS});" 2>/dev/null || true
}

# Remove host rows whose timestamp no longer exists in conntrack_samples (after row-count trim).
metrics_host_trim_orphans() {
  sqlite3 "$METRICS_DB" "DELETE FROM host_samples WHERE ts NOT IN (SELECT ts FROM conntrack_samples);" 2>/dev/null || true
}

# Emit a non-negative integer or the SQL keyword NULL (no quotes).
metrics_sql_uint_or_null() {
  [[ "$1" =~ ^[0-9]+$ ]] && printf '%s' "$1" || printf 'NULL'
}

# Signed/unsigned integer for optional host columns.
metrics_sql_int_or_null() {
  [[ "$1" =~ ^-?[0-9]+$ ]] && printf '%s' "$1" || printf 'NULL'
}

# Non-negative decimal for load1 / shaper rate.
metrics_sql_real_or_null() {
  [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$|^[0-9]*\.[0-9]+$ ]] && printf '%s' "$1" || printf 'NULL'
}

# SQL string literal or NULL (escape ' as '').
metrics_sql_quoted_text_or_null() {
  local s=$1
  if [[ -z "$s" ]]; then
    printf 'NULL'
    return 0
  fi
  local esc="${s//\'/\'\'}"
  printf "'%s'" "$esc"
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

  if ! command -v python3 >/dev/null 2>&1; then
    echo "check-conntrack: python3 is required (cock_monitor domain)" >&2
    exit 1
  fi

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
  la_last_ts=$(state_get la_last_ts)
  [[ "$la_last_ts" =~ ^[0-9]+$ ]] || la_last_ts=0

  local _cock_metrics_decide_done=0
  local fill_severity=0
  local stats_note=""
  local has_ct=0
  local drop_sum=0 if_sum=0 ed_sum=0 er_sum=0 inv_sum=0 sr_sum=0
  local fp_sql fc_sql fm_sql

  if [[ "$CHECK_CONNTRACK_FILL" == "1" ]]; then
    compute_fill_severity || exit 1
    fill_severity=$FILL_SEVERITY

    if [[ "$INCLUDE_CONNTRACK_STATS_LINE" == "1" ]] && command -v conntrack >/dev/null 2>&1; then
      stats_note=$(conntrack_stats_line)
    fi

    if [[ "$fill_severity" -eq 0 ]]; then
      fill_last_severity=0
    else
      local fill_should_send=0
      eval "$(_cock_conntrack_decide --shell <<EOF
{"phase":"fill","now_ts":$(now_epoch),"fill_severity":${fill_severity},"fill_last_ts":${fill_last_ts},"fill_last_severity":${fill_last_severity},"cooldown_seconds":${COOLDOWN_SECONDS}}
EOF
)" || exit 1
      if [[ "$fill_should_send" -eq 1 ]]; then
        local label body
        if [[ "$fill_severity" -eq 2 ]]; then
          label="CRITICAL"
        else
          label="WARNING"
        fi
        local moscow_time; moscow_time=$(TZ='Europe/Moscow' date +'%Y-%m-%d %H:%M:%S MSK')
        body="${label} conntrack fill on ${host} (${moscow_time})"$'\n'"${FILL_PCT}% (${FILL_COUNT}/${FILL_MAX}) warn>=${WARN_PERCENT}% crit>=${CRIT_PERCENT}%"
        [[ -n "$stats_note" ]] && body+=$'\n'"${stats_note}"
        send_telegram "$body" || exit 1
        fill_last_ts=$(now_epoch)
        fill_last_severity=$fill_severity
      fi
    fi
    fp_sql=$FILL_PCT
    fc_sql=$FILL_COUNT
    fm_sql=$FILL_MAX
  else
    fp_sql="NULL"
    fc_sql="NULL"
    fm_sql="NULL"
  fi

  if command -v conntrack >/dev/null 2>&1; then
    has_ct=1
    drop_sum=$(sum_conntrack_stat drop)
    if_sum=$(sum_conntrack_stat insert_failed)
    ed_sum=$(sum_conntrack_stat early_drop)
    er_sum=$(sum_conntrack_stat error)
    inv_sum=$(sum_conntrack_stat invalid)
    sr_sum=$(sum_conntrack_stat search_restart)
  fi

  local now_ts interval_sec="" dd="" di="" de="" derr="" dinv="" dsr=""
  now_ts=$(now_epoch)

  if [[ "$DRY_RUN" != "1" ]] && metrics_wanted; then
    if ! command -v sqlite3 >/dev/null 2>&1; then
      echo "check-conntrack: sqlite3 not found; metrics and delta alerts skipped" >&2
    else
      local dbdir
      dbdir=$(dirname "$METRICS_DB")
      mkdir -p "$dbdir" 2>/dev/null || true
      metrics_init_db
      local prev_line p_ts p_drop p_if p_ed p_er p_inv p_sr
      prev_line=$(metrics_read_last)
      if [[ -n "$prev_line" ]]; then
        IFS='|' read -r p_ts p_drop p_if p_ed p_er p_inv p_sr <<<"$prev_line"
        [[ "$p_ts" =~ ^[0-9]+$ ]] || p_ts=""
      fi
      eval "$(_cock_conntrack_decide --shell <<EOF
{"phase":"metrics","now_ts":${now_ts},"has_conntrack":$(_cock_json_bool "${has_ct}"),"p_ts":$(_cock_json_uint_or_null "${p_ts:-}"),"p_drop":$(_cock_json_uint_or_null "${p_drop:-}"),"p_if":$(_cock_json_uint_or_null "${p_if:-}"),"p_ed":$(_cock_json_uint_or_null "${p_ed:-}"),"p_er":$(_cock_json_uint_or_null "${p_er:-}"),"p_inv":$(_cock_json_uint_or_null "${p_inv:-}"),"p_sr":$(_cock_json_uint_or_null "${p_sr:-}"),"drop_sum":${drop_sum},"if_sum":${if_sum},"ed_sum":${ed_sum},"er_sum":${er_sum},"inv_sum":${inv_sum},"sr_sum":${sr_sum},"alert_on_stats":$(_cock_json_bool "${ALERT_ON_STATS:-0}"),"alert_on_stats_delta":$(_cock_json_bool "${ALERT_ON_STATS_DELTA:-0}"),"stats_last_ts":${stats_last_ts},"stats_cooldown_seconds":${STATS_COOLDOWN_SECONDS},"stats_drop_min":${STATS_DROP_MIN:-0},"stats_insert_failed_min":${STATS_INSERT_FAILED_MIN:-0},"stats_delta_min_interval_sec":${STATS_DELTA_MIN_INTERVAL_SEC:-60},"stats_delta_drop_min":${STATS_DELTA_DROP_MIN:-0},"stats_delta_insert_failed_min":${STATS_DELTA_INSERT_FAILED_MIN:-0},"stats_delta_early_drop_min":${STATS_DELTA_EARLY_DROP_MIN:-0},"stats_delta_error_min":${STATS_DELTA_ERROR_MIN:-0},"stats_delta_invalid_min":${STATS_DELTA_INVALID_MIN:-0},"stats_delta_search_restart_min":${STATS_DELTA_SEARCH_RESTART_MIN:-0},"stats_rate_drop_per_min":${STATS_RATE_DROP_PER_MIN:-0},"stats_rate_insert_failed_per_min":${STATS_RATE_INSERT_FAILED_PER_MIN:-0},"stats_rate_early_drop_per_min":${STATS_RATE_EARLY_DROP_PER_MIN:-0},"stats_rate_error_per_min":${STATS_RATE_ERROR_PER_MIN:-0},"stats_rate_invalid_per_min":${STATS_RATE_INVALID_PER_MIN:-0},"stats_rate_search_restart_per_min":${STATS_RATE_SEARCH_RESTART_PER_MIN:-0}}
EOF
)" || exit 1
      _cock_metrics_decide_done=1

      local do_insert=0
      if [[ "$METRICS_RECORD_EVERY_RUN" == "1" || "$ALERT_ON_STATS_DELTA" == "1" ]]; then
        do_insert=1
        if [[ "$METRICS_RECORD_MIN_INTERVAL_SEC" =~ ^[0-9]+$ && "$METRICS_RECORD_MIN_INTERVAL_SEC" -gt 0 && -n "${p_ts:-}" ]]; then
          if ((now_ts - p_ts < METRICS_RECORD_MIN_INTERVAL_SEC)); then
            do_insert=0
          fi
        fi
      fi

      if [[ "$do_insert" -eq 1 ]]; then
        metrics_collect_host_for_db
        local hl hm hsw hti hto htt h6 hr hc htcq
        hl=$(metrics_sql_real_or_null "${METRICS_DB_LOAD1:-}")
        hm=$(metrics_sql_int_or_null "${METRICS_DB_MEM_AVAIL_KB:-}")
        hsw=$(metrics_sql_int_or_null "${METRICS_DB_SWAP_USED_KB:-}")
        hti=$(metrics_sql_int_or_null "${METRICS_DB_TCP_INUSE:-}")
        hto=$(metrics_sql_int_or_null "${METRICS_DB_TCP_ORPHAN:-}")
        htt=$(metrics_sql_int_or_null "${METRICS_DB_TCP_TW:-}")
        h6=$(metrics_sql_int_or_null "${METRICS_DB_TCP6_INUSE:-}")
        hr=$(metrics_sql_real_or_null "${METRICS_DB_SHAPER_RATE_MBIT:-}")
        hc=$(metrics_sql_int_or_null "${METRICS_DB_SHAPER_CPU_PCT:-}")
        htcq=$(metrics_sql_quoted_text_or_null "${METRICS_DB_TC_QDISC_ROOT:-}")

        if [[ "$has_ct" -eq 1 ]]; then
          local iv_sql sql_dd sql_di sql_de sql_derr sql_dinv sql_dsr
          if [[ -n "$interval_sec" && "$interval_sec" -gt 0 ]]; then
            iv_sql=$interval_sec
            sql_dd=$(metrics_sql_uint_or_null "$dd")
            sql_di=$(metrics_sql_uint_or_null "$di")
            sql_de=$(metrics_sql_uint_or_null "$de")
            sql_derr=$(metrics_sql_uint_or_null "$derr")
            sql_dinv=$(metrics_sql_uint_or_null "$dinv")
            sql_dsr=$(metrics_sql_uint_or_null "$dsr")
          else
            iv_sql="NULL"
            sql_dd="NULL"
            sql_di="NULL"
            sql_de="NULL"
            sql_derr="NULL"
            sql_dinv="NULL"
            sql_dsr="NULL"
          fi
          sqlite3 "$METRICS_DB" <<SQL
BEGIN IMMEDIATE;
INSERT INTO conntrack_samples (ts, fill_pct, fill_count, fill_max, "drop", insert_failed, early_drop, "error", invalid, search_restart, interval_sec, delta_drop, delta_insert_failed, delta_early_drop, delta_error, delta_invalid, delta_search_restart)
VALUES (${now_ts}, ${fp_sql}, ${fc_sql}, ${fm_sql}, ${drop_sum}, ${if_sum}, ${ed_sum}, ${er_sum}, ${inv_sum}, ${sr_sum}, ${iv_sql}, ${sql_dd}, ${sql_di}, ${sql_de}, ${sql_derr}, ${sql_dinv}, ${sql_dsr});
INSERT INTO host_samples (ts, load1, mem_avail_kb, swap_used_kb, tcp_inuse, tcp_orphan, tcp_tw, tcp6_inuse, shaper_rate_mbit, shaper_cpu_pct, tc_qdisc_root)
VALUES (${now_ts}, ${hl}, ${hm}, ${hsw}, ${hti}, ${hto}, ${htt}, ${h6}, ${hr}, ${hc}, ${htcq});
COMMIT;
SQL
        else
          sqlite3 "$METRICS_DB" <<SQL
BEGIN IMMEDIATE;
INSERT INTO conntrack_samples (ts, fill_pct, fill_count, fill_max, "drop", insert_failed, early_drop, "error", invalid, search_restart, interval_sec, delta_drop, delta_insert_failed, delta_early_drop, delta_error, delta_invalid, delta_search_restart)
VALUES (${now_ts}, ${fp_sql}, ${fc_sql}, ${fm_sql}, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO host_samples (ts, load1, mem_avail_kb, swap_used_kb, tcp_inuse, tcp_orphan, tcp_tw, tcp6_inuse, shaper_rate_mbit, shaper_cpu_pct, tc_qdisc_root)
VALUES (${now_ts}, ${hl}, ${hm}, ${hsw}, ${hti}, ${hto}, ${htt}, ${h6}, ${hr}, ${hc}, ${htcq});
COMMIT;
SQL
        fi
        metrics_retention
        metrics_trim_max_rows
        metrics_host_trim_orphans
      fi
    fi
  fi

  # --- Load Average alert ---
  if [[ "$LA_ALERT_ENABLE" == "1" ]]; then
    local la1
    la1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo "0")
    if awk "BEGIN { exit !(${la1} >= ${LA_WARN_THRESHOLD}) }" 2>/dev/null; then
      local now_la
      now_la=$(now_epoch)
      if ((now_la - la_last_ts >= LA_ALERT_COOLDOWN_SEC)); then
        local ncpu
        ncpu=$(nproc 2>/dev/null || echo "?")

        # --- CPU% and shaper state from status file (no extra delay) ---
        local s_cpu="?" s_rate="?" s_op="hold" s_iface="ens3"
        local shaper_status_f="${SHAPER_STATUS_FILE:-/var/lib/cock-monitor/cpu_shaper.status}"
        if [[ -f "$shaper_status_f" ]]; then
          local _k _v
          while IFS='=' read -r _k _v; do
            case "$_k" in
              cpu_pct)          s_cpu="$_v"   ;;
              rate_applied_mbit) s_rate="$_v" ;;
              tc_op)            s_op="$_v"    ;;
              iface)            s_iface="$_v" ;;
            esac
          done < "$shaper_status_f"
        fi
        local op_label="стабильно"
        [[ "$s_op" == "step_down" ]] && op_label="ограничение ↓"
        [[ "$s_op" == "step_up"   ]] && op_label="восстановление ↑"

        # --- Network TX/RX rate via 1-second /proc/net/dev delta ---
        local tx_mbit="?" rx_mbit="?"
        if [[ -f /proc/net/dev ]]; then
          local rx1 tx1 rx2 tx2
          rx1=$(awk -v dev="${s_iface}:" '$1==dev {print $2}' /proc/net/dev 2>/dev/null || echo "")
          tx1=$(awk -v dev="${s_iface}:" '$1==dev {print $10}' /proc/net/dev 2>/dev/null || echo "")
          sleep 1
          rx2=$(awk -v dev="${s_iface}:" '$1==dev {print $2}' /proc/net/dev 2>/dev/null || echo "")
          tx2=$(awk -v dev="${s_iface}:" '$1==dev {print $10}' /proc/net/dev 2>/dev/null || echo "")
          if [[ "$rx1" =~ ^[0-9]+$ && "$rx2" =~ ^[0-9]+$ && "$rx2" -ge "$rx1" ]]; then
            rx_mbit=$(( (rx2 - rx1) * 8 / 1000000 ))
          fi
          if [[ "$tx1" =~ ^[0-9]+$ && "$tx2" =~ ^[0-9]+$ && "$tx2" -ge "$tx1" ]]; then
            tx_mbit=$(( (tx2 - tx1) * 8 / 1000000 ))
          fi
        fi

        local la_body
        local moscow_time; moscow_time=$(TZ='Europe/Moscow' date +'%Y-%m-%d %H:%M:%S MSK')
        la_body="⚠️ High Load Average on ${host} (${moscow_time})"$'\n'
        la_body+="la1=${la1} (порог: >=${LA_WARN_THRESHOLD}, vCPU: ${ncpu})"$'\n'
        la_body+="CPU: ${s_cpu}% | Шейпер: ${op_label} @ ${s_rate} Mbit/s"$'\n'
        la_body+="Трафик ${s_iface}: исходящий (к клиентам) ${tx_mbit} Mbit/s | входящий (от клиентов) ${rx_mbit} Mbit/s"
        send_telegram "$la_body" || exit 1
        la_last_ts=$now_la
      fi
    fi
  fi

  if [[ "$_cock_metrics_decide_done" -eq 0 ]]; then
    eval "$(_cock_conntrack_decide --shell <<EOF
{"phase":"metrics","now_ts":${now_ts},"has_conntrack":$(_cock_json_bool "${has_ct}"),"p_ts":null,"p_drop":null,"p_if":null,"p_ed":null,"p_er":null,"p_inv":null,"p_sr":null,"drop_sum":${drop_sum},"if_sum":${if_sum},"ed_sum":${ed_sum},"er_sum":${er_sum},"inv_sum":${inv_sum},"sr_sum":${sr_sum},"alert_on_stats":$(_cock_json_bool "${ALERT_ON_STATS:-0}"),"alert_on_stats_delta":$(_cock_json_bool "${ALERT_ON_STATS_DELTA:-0}"),"stats_last_ts":${stats_last_ts},"stats_cooldown_seconds":${STATS_COOLDOWN_SECONDS},"stats_drop_min":${STATS_DROP_MIN:-0},"stats_insert_failed_min":${STATS_INSERT_FAILED_MIN:-0},"stats_delta_min_interval_sec":${STATS_DELTA_MIN_INTERVAL_SEC:-60},"stats_delta_drop_min":${STATS_DELTA_DROP_MIN:-0},"stats_delta_insert_failed_min":${STATS_DELTA_INSERT_FAILED_MIN:-0},"stats_delta_early_drop_min":${STATS_DELTA_EARLY_DROP_MIN:-0},"stats_delta_error_min":${STATS_DELTA_ERROR_MIN:-0},"stats_delta_invalid_min":${STATS_DELTA_INVALID_MIN:-0},"stats_delta_search_restart_min":${STATS_DELTA_SEARCH_RESTART_MIN:-0},"stats_rate_drop_per_min":${STATS_RATE_DROP_PER_MIN:-0},"stats_rate_insert_failed_per_min":${STATS_RATE_INSERT_FAILED_PER_MIN:-0},"stats_rate_early_drop_per_min":${STATS_RATE_EARLY_DROP_PER_MIN:-0},"stats_rate_error_per_min":${STATS_RATE_ERROR_PER_MIN:-0},"stats_rate_invalid_per_min":${STATS_RATE_INVALID_PER_MIN:-0},"stats_rate_search_restart_per_min":${STATS_RATE_SEARCH_RESTART_PER_MIN:-0}}
EOF
)" || exit 1
  fi

  if [[ "$has_ct" -eq 1 ]]; then
    if [[ "$stats_fire" -eq 1 ]]; then
      local stats_body
      local moscow_time; moscow_time=$(TZ='Europe/Moscow' date +'%Y-%m-%d %H:%M:%S MSK')
      stats_body="STATS ${host} (${moscow_time})"$'\n'"${stats_reason}"$'\n'"$(conntrack_stats_line)"$'\n'"$(format_stats_alert_host_context)"
      if [[ "${stats_send_telegram:-0}" -eq 1 ]]; then
        send_telegram "$stats_body" || exit 1
        stats_last_ts=$(now_epoch)
      fi
    fi
  fi

  state_write || true

  exit 0
}

main "$@"
