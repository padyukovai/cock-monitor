#!/bin/bash
# Stabilize MTProxy VPS: connlimit, sysctl, swap, sshd, mtproto tuning, metrics override.
# Run on the VPS as root: bash stabilize-vps.sh
#
# Env overrides:
#   CONNLIMIT=0          disable per-IP connlimit (default; was 35, breaks Telegram reconnects)
#   CONNLIMIT=35         re-enable connlimit if needed
#   SWAP_SIZE=1G
#   COCK_MONITOR_HOME=/opt/cock-monitor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONNLIMIT="${CONNLIMIT:-0}"
SWAP_SIZE="${SWAP_SIZE:-1G}"
COCK_MONITOR_HOME="${COCK_MONITOR_HOME:-/opt/cock-monitor}"
RUN_SCRIPT="/usr/local/bin/mtproto-run.sh"
RESOURCE_LIMITS="/etc/systemd/system/mtproto.service.d/resource-limits.conf"
SYSCTL_FILE="/etc/sysctl.d/99-mtproxy-net.conf"
IPTABLES_RULES="/etc/iptables/rules.v4"
IPTABLES_RESTORE_UNIT="/etc/systemd/system/mtproto-iptables-restore.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

echo "=== Step 9 prep: diagnose metrics collector ==="
journalctl -u cock-mtproxy-monitor.service -n 15 --no-pager 2>/dev/null || true

echo
echo "=== Step 1: iptables connlimit on :8443 (limit=${CONNLIMIT}, 0=disabled) ==="
if iptables -C INPUT -p tcp --dport 8443 -m connlimit --connlimit-above 35 --connlimit-mask 32 -j REJECT --reject-with tcp-reset 2>/dev/null; then
  iptables -D INPUT -p tcp --dport 8443 -m connlimit --connlimit-above 35 --connlimit-mask 32 -j REJECT --reject-with tcp-reset
  echo "removed legacy connlimit rule (limit 35)"
fi
if [[ "$CONNLIMIT" -gt 0 ]]; then
  if ! iptables -C INPUT -p tcp --dport 8443 -m connlimit --connlimit-above "$CONNLIMIT" --connlimit-mask 32 -j REJECT --reject-with tcp-reset 2>/dev/null; then
    iptables -I INPUT 1 -p tcp --dport 8443 \
      -m connlimit --connlimit-above "$CONNLIMIT" --connlimit-mask 32 \
      -j REJECT --reject-with tcp-reset
    echo "connlimit rule added (limit=${CONNLIMIT})"
  else
    echo "connlimit rule already present"
  fi
else
  echo "connlimit disabled (CONNLIMIT=0)"
fi

mkdir -p /etc/iptables
iptables-save > "$IPTABLES_RULES"
chmod 600 "$IPTABLES_RULES"

cat > "$IPTABLES_RESTORE_UNIT" <<EOF
[Unit]
Description=Restore iptables rules (cock-monitor / MTProxy)
After=network-pre.target
Before=network-online.target
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore $IPTABLES_RULES
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable mtproto-iptables-restore.service

echo
echo "=== Steps 2-3: update mtproto-run.sh (-c 3000, max-accept-rate 50) ==="
if [[ -f "$RUN_SCRIPT" ]]; then
  cp -a "$RUN_SCRIPT" "${RUN_SCRIPT}.bak.$(date +%Y%m%d%H%M%S)"
fi
install -m755 "${SCRIPT_DIR}/mtproto-run.sh" "$RUN_SCRIPT"

echo
echo "=== Steps 4+8: conntrack + tcp_max_syn_backlog + swappiness ==="
echo nf_conntrack > /etc/modules-load.d/nf-conntrack.conf
modprobe nf_conntrack 2>/dev/null || true

cat > "$SYSCTL_FILE" <<EOF
net.netfilter.nf_conntrack_max = 65536
net.ipv4.tcp_max_syn_backlog = 4096
vm.swappiness = 10
EOF
sysctl --system >/dev/null 2>&1 || sysctl -p "$SYSCTL_FILE"

echo
echo "=== Step 5: swap ${SWAP_SIZE} ==="
if ! swapon --show | grep -q '/swapfile'; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  echo "swap enabled"
else
  echo "swap already active"
fi

echo
echo "=== Step 6: sshd hardening drop-in ==="
cat > /etc/ssh/sshd_config.d/99-cock-monitor-hardening.conf <<EOF
UseDNS no
GSSAPIAuthentication no
MaxStartups 30:50:200
EOF
sshd -t
systemctl reload ssh

echo
echo "=== Step 7: remove CPUQuota from mtproto ==="
if [[ -f "$RESOURCE_LIMITS" ]]; then
  cp -a "$RESOURCE_LIMITS" "${RESOURCE_LIMITS}.bak.$(date +%Y%m%d%H%M%S)"
  grep -v -E '^(CPUQuota|CPUWeight)=' "$RESOURCE_LIMITS" > "${RESOURCE_LIMITS}.tmp"
  mv "${RESOURCE_LIMITS}.tmp" "$RESOURCE_LIMITS"
else
  mkdir -p "$(dirname "$RESOURCE_LIMITS")"
  cat > "$RESOURCE_LIMITS" <<EOF
[Service]
CPUAccounting=yes
MemoryHigh=160M
MemoryMax=280M
Nice=5
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF
fi

echo
echo "=== Step 9: systemd override for mtproxy metrics (.venv python) ==="
VENV_PYTHON="${COCK_MONITOR_HOME}/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "WARNING: $VENV_PYTHON not found; metrics override skipped" >&2
else
  for svc in cock-mtproxy-monitor cock-mtproxy-daily; do
    dropin="/etc/systemd/system/${svc}.service.d"
    mkdir -p "$dropin"
    if [[ "$svc" == "cock-mtproxy-monitor" ]]; then
      exec_line="${VENV_PYTHON} -m cock_monitor mtproxy-collect --env-file /etc/cock-monitor.env"
    else
      exec_line="${VENV_PYTHON} -m cock_monitor mtproxy-daily --env-file /etc/cock-monitor.env --hours 24 --send-telegram"
    fi
    cat > "${dropin}/override.conf" <<EOF
[Service]
WorkingDirectory=${COCK_MONITOR_HOME}
ExecStart=
ExecStart=${exec_line}
EOF
  done

  if [[ -f /etc/cock-monitor.env ]] && ! grep -q '^MTPROXY_CONNTRACK_ENABLE=' /etc/cock-monitor.env; then
    echo 'MTPROXY_CONNTRACK_ENABLE=1' >> /etc/cock-monitor.env
  elif [[ -f /etc/cock-monitor.env ]]; then
    sed -i 's/^MTPROXY_CONNTRACK_ENABLE=.*/MTPROXY_CONNTRACK_ENABLE=1/' /etc/cock-monitor.env
  fi
fi

echo
echo "=== Restart services ==="
systemctl daemon-reload
systemctl restart mtproto.service
sleep 2
if ! systemctl is-active --quiet mtproto.service; then
  echo "ERROR: mtproto.service failed" >&2
  journalctl -u mtproto.service -n 30 --no-pager
  exit 1
fi

if [[ -x "$VENV_PYTHON" ]]; then
  systemctl restart cock-mtproxy-monitor.timer 2>/dev/null || true
  systemctl start cock-mtproxy-monitor.service 2>/dev/null || true
fi

echo
echo "=== Verification ==="
echo -n "mtproto: "; systemctl is-active mtproto.service
echo -n "conntrack_max: "; cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo n/a
echo -n "tcp_max_syn_backlog: "; sysctl -n net.ipv4.tcp_max_syn_backlog
echo -n "swap: "; swapon --show | tail -n +2 || echo none
echo -n "connlimit rule: "; iptables -L INPUT -n --line-numbers | grep -E '8443|connlimit' | head -3
pgrep -af mtproto-proxy || true

if [[ -x "$VENV_PYTHON" ]]; then
  echo -n "mtproxy_metrics rows: "
  sqlite3 /var/lib/cock-monitor/metrics.db "SELECT count(*) FROM mtproxy_metrics;" 2>/dev/null || echo n/a
fi

echo
echo "Done."
