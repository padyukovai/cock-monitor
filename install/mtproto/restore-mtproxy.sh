#!/bin/bash
# MTProxy install (FakeTLS only). Run as root:
#   PUBLIC_IP=163.5.153.32 bash install/mtproto/restore-mtproxy.sh
#
# Requires install/mtproto/restore-data/server-secrets-faketls.txt and faketls-domain.conf
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESTORE_DATA="${SCRIPT_DIR}/restore-data"
PUBLIC_IP="${PUBLIC_IP:?Set PUBLIC_IP (e.g. 163.5.153.32)}"
MTPROXY_PORT="${MTPROXY_PORT:-8443}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

for f in server-secrets-faketls.txt faketls-domain.conf; do
  if [[ ! -f "${RESTORE_DATA}/${f}" ]]; then
    echo "Missing ${RESTORE_DATA}/${f}" >&2
    exit 1
  fi
done

echo "=== packages ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl build-essential libssl-dev zlib1g-dev iputils-ping

echo "=== MTProxy (GetPageSpeed, direct mode) ==="
curl -fsSL -o /usr/local/bin/mtproto-proxy \
  https://github.com/GetPageSpeed/MTProxy/releases/download/v3.5.5/mtproto-proxy-linux-amd64
chmod +x /usr/local/bin/mtproto-proxy

echo "=== /etc/mtproto ==="
install -d -m700 /etc/mtproto
install -m600 "${RESTORE_DATA}/server-secrets-faketls.txt" /etc/mtproto/server-secrets-faketls.txt
install -m600 "${RESTORE_DATA}/faketls-domain.conf" /etc/mtproto/faketls-domain.conf
rm -f /etc/mtproto/server-secrets.txt /etc/mtproto/client-links-plain.txt

curl -fsS --max-time 30 https://core.telegram.org/getProxySecret -o /etc/mtproto/proxy-secret
chmod 600 /etc/mtproto/proxy-secret

echo "=== DNS (FakeTLS needs www.google.com resolve at start) ==="
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/public-dns.conf <<'EOF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9
EOF
systemctl restart systemd-resolved

echo "=== scripts ==="
install -m755 "${SCRIPT_DIR}/mtproto-run.sh" /usr/local/bin/mtproto-run.sh
install -m755 "${SCRIPT_DIR}/mtproto-show-faketls-links.sh" /usr/local/bin/mtproto-show-faketls-links.sh
install -m755 "${SCRIPT_DIR}/mtproto-update-config.sh" /usr/local/bin/mtproto-update-config.sh

echo "=== systemd mtproto.service ==="
cat > /etc/systemd/system/mtproto.service <<EOF
[Unit]
Description=Telegram MTProxy FakeTLS (port ${MTPROXY_PORT})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=MTPROXY_PORT=${MTPROXY_PORT}
ExecStart=/usr/local/bin/mtproto-run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/systemd/system/mtproto.service.d
cat > /etc/systemd/system/mtproto.service.d/resource-limits.conf <<'EOF'
[Service]
CPUAccounting=yes
MemoryHigh=160M
MemoryMax=280M
Nice=5
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF

echo "=== done (direct mode, no config refresh timer) ==="
printf '%s\n' 'kernel.pid_max = 65535' > /etc/sysctl.d/99-mtproxy-pid-max.conf
sysctl -p /etc/sysctl.d/99-mtproxy-pid-max.conf

systemctl daemon-reload
systemctl enable --now mtproto.service
sleep 2

if ! systemctl is-active --quiet mtproto.service; then
  echo "ERROR: mtproto failed to start" >&2
  journalctl -u mtproto.service -n 40 --no-pager
  exit 1
fi

echo
echo "=== FakeTLS links ==="
/usr/local/bin/mtproto-show-faketls-links.sh "${PUBLIC_IP}" "${MTPROXY_PORT}" | tee /etc/mtproto/client-links-faketls.txt
chmod 600 /etc/mtproto/client-links-faketls.txt

echo
echo "=== OK ==="
echo "IP=${PUBLIC_IP} port=${MTPROXY_PORT} mode=FakeTLS domain=$(tr -d '[:space:]' < /etc/mtproto/faketls-domain.conf)"
pgrep -af mtproto-proxy || true
ss -tlnp | grep -E ":${MTPROXY_PORT}\\b" || true
