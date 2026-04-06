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
  sqlite3 "$METRICS_DB" "DELETE FROM conntrack_samples WHERE ts < ${cutoff};" 2>/dev/null || true
}

metrics_trim_max_rows() {
  [[ "$METRICS_MAX_ROWS" =~ ^[0-9]+$ && "$METRICS_MAX_ROWS" -gt 0 ]] || return 0
  sqlite3 "$METRICS_DB" "DELETE FROM conntrack_samples WHERE id NOT IN (SELECT id FROM conntrack_samples ORDER BY id DESC LIMIT ${METRICS_MAX_ROWS});" 2>/dev/null || true
}

# Emit a non-negative integer or the SQL keyword NULL (no quotes).
metrics_sql_uint_or_null() {
  [[ "$1" =~ ^[0-9]+$ ]] && printf '%s' "$1" || printf 'NULL'
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
  la_last_ts=$(state_get la_last_ts)
  [[ "$la_last_ts" =~ ^[0-9]+$ ]] || la_last_ts=0

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
    elif should_send_fill_alert "$fill_severity"; then
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
      if [[ "$has_ct" -eq 1 && -n "${p_ts:-}" ]]; then
        interval_sec=$((now_ts - p_ts))
        if [[ "$interval_sec" -gt 0 ]]; then
          [[ "$p_drop" =~ ^[0-9]+$ ]] || p_drop=0
          [[ "$p_if" =~ ^[0-9]+$ ]] || p_if=0
          [[ "$p_ed" =~ ^[0-9]+$ ]] || p_ed=0
          [[ "$p_er" =~ ^[0-9]+$ ]] || p_er=0
          [[ "$p_inv" =~ ^[0-9]+$ ]] || p_inv=0
          [[ "$p_sr" =~ ^[0-9]+$ ]] || p_sr=0
          dd=$(u32_counter_delta "$p_drop" "$drop_sum")
          di=$(u32_counter_delta "$p_if" "$if_sum")
          de=$(u32_counter_delta "$p_ed" "$ed_sum")
          derr=$(u32_counter_delta "$p_er" "$er_sum")
          dinv=$(u32_counter_delta "$p_inv" "$inv_sum")
          dsr=$(u32_counter_delta "$p_sr" "$sr_sum")
        fi
      fi

      local do_insert=0
      if [[ "$METRICS_RECORD_EVERY_RUN" == "1" || "$ALERT_ON_STATS_DELTA" == "1" ]]; then
        do_insert=1
        if [[ "$METRICS_RECORD_MIN_INTERVAL_SEC" =~ ^[0-9]+$ && "$METRICS_RECORD_MIN_INTERVAL_SEC" -gt 0 && -n "${p_ts:-}" ]]; then
          if ((now_ts - p_ts < METRICS_RECORD_MIN_INTERVAL_SEC)); then
            do_insert=0
          fi
        fi
      fi

      if [[ "$do_insert" -eq 1 && "$has_ct" -eq 1 ]]; then
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
INSERT INTO conntrack_samples (ts, fill_pct, fill_count, fill_max, "drop", insert_failed, early_drop, "error", invalid, search_restart, interval_sec, delta_drop, delta_insert_failed, delta_early_drop, delta_error, delta_invalid, delta_search_restart)
VALUES (${now_ts}, ${fp_sql}, ${fc_sql}, ${fm_sql}, ${drop_sum}, ${if_sum}, ${ed_sum}, ${er_sum}, ${inv_sum}, ${sr_sum}, ${iv_sql}, ${sql_dd}, ${sql_di}, ${sql_de}, ${sql_derr}, ${sql_dinv}, ${sql_dsr});
SQL
        metrics_retention
        metrics_trim_max_rows
      elif [[ "$do_insert" -eq 1 && "$has_ct" -eq 0 ]]; then
        sqlite3 "$METRICS_DB" <<SQL
INSERT INTO conntrack_samples (ts, fill_pct, fill_count, fill_max, "drop", insert_failed, early_drop, "error", invalid, search_restart, interval_sec, delta_drop, delta_insert_failed, delta_early_drop, delta_error, delta_invalid, delta_search_restart)
VALUES (${now_ts}, ${fp_sql}, ${fc_sql}, ${fm_sql}, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
SQL
        metrics_retention
        metrics_trim_max_rows
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

  local stats_fire=0 stats_reason=""

  if [[ "$has_ct" -eq 1 ]]; then
    if [[ "$ALERT_ON_STATS" == "1" ]]; then
      if [[ "$STATS_DROP_MIN" =~ ^[0-9]+$ && "$STATS_DROP_MIN" -gt 0 && "$drop_sum" -ge "$STATS_DROP_MIN" ]]; then
        stats_fire=1
        stats_reason="cumulative: drop=${drop_sum} (>=${STATS_DROP_MIN})"
      fi
      if [[ "$STATS_INSERT_FAILED_MIN" =~ ^[0-9]+$ && "$STATS_INSERT_FAILED_MIN" -gt 0 && "$if_sum" -ge "$STATS_INSERT_FAILED_MIN" ]]; then
        stats_fire=1
        stats_reason="${stats_reason:+$stats_reason; }cumulative: insert_failed=${if_sum} (>=${STATS_INSERT_FAILED_MIN})"
      fi
    fi

    if [[ "$ALERT_ON_STATS_DELTA" == "1" && "${interval_sec:-}" =~ ^[0-9]+$ && "$interval_sec" -gt 0 && "$interval_sec" -ge "${STATS_DELTA_MIN_INTERVAL_SEC:-60}" ]]; then
      [[ "$STATS_DELTA_MIN_INTERVAL_SEC" =~ ^[0-9]+$ ]] || STATS_DELTA_MIN_INTERVAL_SEC=60
      local rd=0 ri=0 re=0 rerr=0 rin=0 rsr=0
      [[ "$dd" =~ ^[0-9]+$ ]] && rd=$((dd * 60 / interval_sec))
      [[ "$di" =~ ^[0-9]+$ ]] && ri=$((di * 60 / interval_sec))
      [[ "$de" =~ ^[0-9]+$ ]] && re=$((de * 60 / interval_sec))
      [[ "$derr" =~ ^[0-9]+$ ]] && rerr=$((derr * 60 / interval_sec))
      [[ "$dinv" =~ ^[0-9]+$ ]] && rin=$((dinv * 60 / interval_sec))
      [[ "$dsr" =~ ^[0-9]+$ ]] && rsr=$((dsr * 60 / interval_sec))
      local dpart=0
      if [[ "$dd" =~ ^[0-9]+$ && "$STATS_DELTA_DROP_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_DROP_MIN" -gt 0 && "$dd" -ge "$STATS_DELTA_DROP_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_DROP_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_DROP_PER_MIN" -gt 0 && "$rd" -ge "$STATS_RATE_DROP_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$di" =~ ^[0-9]+$ && "$STATS_DELTA_INSERT_FAILED_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_INSERT_FAILED_MIN" -gt 0 && "$di" -ge "$STATS_DELTA_INSERT_FAILED_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_INSERT_FAILED_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_INSERT_FAILED_PER_MIN" -gt 0 && "$ri" -ge "$STATS_RATE_INSERT_FAILED_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$de" =~ ^[0-9]+$ && "$STATS_DELTA_EARLY_DROP_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_EARLY_DROP_MIN" -gt 0 && "$de" -ge "$STATS_DELTA_EARLY_DROP_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_EARLY_DROP_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_EARLY_DROP_PER_MIN" -gt 0 && "$re" -ge "$STATS_RATE_EARLY_DROP_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$derr" =~ ^[0-9]+$ && "$STATS_DELTA_ERROR_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_ERROR_MIN" -gt 0 && "$derr" -ge "$STATS_DELTA_ERROR_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_ERROR_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_ERROR_PER_MIN" -gt 0 && "$rerr" -ge "$STATS_RATE_ERROR_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$dinv" =~ ^[0-9]+$ && "$STATS_DELTA_INVALID_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_INVALID_MIN" -gt 0 && "$dinv" -ge "$STATS_DELTA_INVALID_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_INVALID_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_INVALID_PER_MIN" -gt 0 && "$rin" -ge "$STATS_RATE_INVALID_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$dsr" =~ ^[0-9]+$ && "$STATS_DELTA_SEARCH_RESTART_MIN" =~ ^[0-9]+$ && "$STATS_DELTA_SEARCH_RESTART_MIN" -gt 0 && "$dsr" -ge "$STATS_DELTA_SEARCH_RESTART_MIN" ]]; then
        dpart=1
      fi
      if [[ "$STATS_RATE_SEARCH_RESTART_PER_MIN" =~ ^[0-9]+$ && "$STATS_RATE_SEARCH_RESTART_PER_MIN" -gt 0 && "$rsr" -ge "$STATS_RATE_SEARCH_RESTART_PER_MIN" ]]; then
        dpart=1
      fi
      if [[ "$dpart" -eq 1 ]]; then
        stats_fire=1
        stats_reason="${stats_reason:+$stats_reason; }delta (${interval_sec}s): drop+${dd:-?} (~${rd}/min) insert_failed+${di:-?} early_drop+${de:-?} error+${derr:-?} invalid+${dinv:-?} search_restart+${dsr:-?}"
      fi
    fi

    if [[ "$stats_fire" -eq 1 ]]; then
      local stats_body
      local moscow_time; moscow_time=$(TZ='Europe/Moscow' date +'%Y-%m-%d %H:%M:%S MSK')
      stats_body="STATS ${host} (${moscow_time})"$'\n'"${stats_reason}"$'\n'"$(conntrack_stats_line)"
      if should_send_stats_alert; then
        send_telegram "$stats_body" || exit 1
        stats_last_ts=$(now_epoch)
      fi
    fi
  fi

  state_write || true

  exit 0
}

main "$@"
