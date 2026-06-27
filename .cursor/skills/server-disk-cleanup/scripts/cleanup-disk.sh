#!/usr/bin/env bash
# Safe disk cleanup for cock-monitor VPS (small root volumes).
# Idempotent — safe to re-run.
set -euo pipefail

INCIDENT_RETENTION_DAYS="${INCIDENT_RETENTION_DAYS:-14}"
JOURNAL_MAX_SIZE="${JOURNAL_MAX_SIZE:-100M}"

log() { printf '[cleanup] %s\n' "$*"; }

print_usage() {
  echo "=== before ==="
  df -h /
  journalctl --disk-usage 2>/dev/null || true
  du -sh /var/cache/apt /var/lib/apt/lists /var/lib/cock-monitor /root/.cache 2>/dev/null || true
  ls -lh /var/log/btmp* 2>/dev/null || true
}

ensure_journal_limit() {
  local conf=/etc/systemd/journald.conf.d/99-size-limit.conf
  if [[ -f "$conf" ]] && grep -q 'SystemMaxUse=100M' "$conf"; then
    log "journald limit already configured"
    return 0
  fi
  log "setting journald size limit"
  mkdir -p /etc/systemd/journald.conf.d
  cat >"$conf" <<'EOF'
[Journal]
SystemMaxUse=100M
RuntimeMaxUse=50M
EOF
  systemctl restart systemd-journald
}

main() {
  print_usage

  log "vacuuming journal to ${JOURNAL_MAX_SIZE}"
  journalctl --vacuum-size="${JOURNAL_MAX_SIZE}" || true

  log "cleaning apt cache"
  apt-get clean -y 2>/dev/null || apt-get clean
  rm -rf /var/lib/apt/lists/*

  log "truncating btmp logs"
  : > /var/log/btmp 2>/dev/null || true
  : > /var/log/btmp.1 2>/dev/null || true

  log "removing incident logs older than ${INCIDENT_RETENTION_DAYS} days"
  find /var/lib/cock-monitor -maxdepth 1 -name 'incident-*.jsonl' -mtime "+${INCIDENT_RETENTION_DAYS}" -print -delete 2>/dev/null || true

  if [[ -f /var/log/x-ui/3xipl-ap.prev.log ]]; then
    log "truncating x-ui rotated log"
    : > /var/log/x-ui/3xipl-ap.prev.log
  fi

  if [[ -d /root/.cache ]]; then
    log "clearing /root/.cache"
    rm -rf /root/.cache/*
  fi

  ensure_journal_limit

  echo "=== after ==="
  df -h /
  journalctl --disk-usage 2>/dev/null || true
  du -sh /var/lib/cock-monitor /var/cache/apt /var/lib/apt/lists 2>/dev/null || true

  log "service check"
  systemctl is-active mtproto x-ui ssh cock-monitor.timer 2>/dev/null || true
}

main "$@"
