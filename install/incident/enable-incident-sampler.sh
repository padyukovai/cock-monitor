#!/bin/bash
# Enable incident sampler: JSONL network health history (no Telegram required).
# Run on VPS as root: bash enable-incident-sampler.sh
#
# Env overrides:
#   COCK_MONITOR_HOME=/opt/cock-monitor
#   ENV_FILE=/etc/cock-monitor.env
#   PUBLIC_IP=163.5.41.47          external TCP probe target
#   INCIDENT_TCP_PROBE_PORTS="22 8443 443"
set -euo pipefail

COCK_MONITOR_HOME="${COCK_MONITOR_HOME:-/opt/cock-monitor}"
ENV_FILE="${ENV_FILE:-/etc/cock-monitor.env}"
PUBLIC_IP="${PUBLIC_IP:-163.5.41.47}"
PROBE_PORTS="${INCIDENT_TCP_PROBE_PORTS:-22 8443 443}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ! -d "$COCK_MONITOR_HOME" ]]; then
  echo "Missing $COCK_MONITOR_HOME" >&2
  exit 1
fi

VENV_PYTHON="${COCK_MONITOR_HOME}/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing $VENV_PYTHON — run install first." >&2
  exit 1
fi

if ! command -v ping >/dev/null 2>&1; then
  echo "Installing iputils-ping..."
  apt-get update -qq && apt-get install -y -qq iputils-ping
fi

set_env_key() {
  local key="$1"
  local val="$2"
  local file="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

set_env_key INCIDENT_SAMPLER_ENABLE 1 "$ENV_FILE"
set_env_key INCIDENT_ALERT_ENABLE 0 "$ENV_FILE"
set_env_key INCIDENT_POSTMORTEM_ENABLE 0 "$ENV_FILE"
set_env_key INCIDENT_LOG_DIR /var/lib/cock-monitor "$ENV_FILE"
set_env_key INCIDENT_STATE_FILE /var/lib/cock-monitor/incident_sampler.state "$ENV_FILE"
set_env_key INCIDENT_TCP_PROBE_LOCAL_TARGET 127.0.0.1 "$ENV_FILE"
set_env_key INCIDENT_TCP_PROBE_EXTERNAL_TARGET "$PUBLIC_IP" "$ENV_FILE"
set_env_key INCIDENT_TCP_PROBE_PORTS "\"${PROBE_PORTS}\"" "$ENV_FILE"
set_env_key INCIDENT_TCP_PROBE_TIMEOUT_SEC 2 "$ENV_FILE"
set_env_key INCIDENT_SYSTEMD_UNITS "\"mtproto.service ssh.service x-ui.service\"" "$ENV_FILE"
set_env_key INCIDENT_DNS_HOST one.one.one.one "$ENV_FILE"
set_env_key INCIDENT_PING_EXTERNAL_TARGETS "\"1.1.1.1 8.8.8.8\"" "$ENV_FILE"

echo "Installing systemd units..."
install -m644 "${COCK_MONITOR_HOME}/systemd/cock-monitor-incident-sampler.service" /etc/systemd/system/
install -m644 "${COCK_MONITOR_HOME}/systemd/cock-monitor-incident-sampler.timer" /etc/systemd/system/

dropin="/etc/systemd/system/cock-monitor-incident-sampler.service.d"
mkdir -p "$dropin"
cat > "${dropin}/override.conf" <<EOF
[Service]
WorkingDirectory=${COCK_MONITOR_HOME}
ExecStart=
ExecStart=${VENV_PYTHON} -m cock_monitor.services.incident_sampler ${ENV_FILE}
EOF

systemctl daemon-reload
systemctl enable --now cock-monitor-incident-sampler.timer
systemctl start cock-monitor-incident-sampler.service

install -m755 "${COCK_MONITOR_HOME}/install/incident/incident-status.sh" /usr/local/bin/incident-status 2>/dev/null || true

echo
echo "Incident sampler enabled (alerts/postmortem OFF, JSONL only)."
echo "Logs: /var/lib/cock-monitor/incident-\$(date -u +%Y%m%d).jsonl"
echo "Quick view: incident-status   (or: incident-status --last 20)"
echo
if [[ -x /usr/local/bin/incident-status ]]; then
  /usr/local/bin/incident-status --last 3
else
  LOG="/var/lib/cock-monitor/incident-$(date -u +%Y%m%d).jsonl"
  [[ -f "$LOG" ]] && tail -1 "$LOG" || echo "(no samples yet)"
fi
