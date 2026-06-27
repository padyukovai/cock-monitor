#!/usr/bin/env bash
# RF3: persistent xray-hop-probe (local SOCKS ingress -> VLESS outbounds) + cleanup stale test xray.
set -euo pipefail

XRAY_BIN="${XRAY_BIN:-/usr/local/x-ui/bin/xray-linux-amd64}"
PROD_CONFIG="${PROD_CONFIG:-/usr/local/x-ui/bin/config.json}"
PROBE_CONFIG_DIR="/etc/xray-hop-probe"
PROBE_CONFIG="${PROBE_CONFIG_DIR}/config.json"
SOCKS_DE_PORT="${HOP_PROBE_SOCKS_DE_PORT:-10891}"
SOCKS_USA_PORT="${HOP_PROBE_SOCKS_USA_PORT:-10892}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run: sudo bash install/rf3/setup-hop-probe.sh" >&2
  exit 1
fi

echo "=== Cleanup stale test xray (/tmp/tmp*.json) ==="
for pid in $(pgrep -f 'xray-linux-amd64 run -c /tmp/tmp' 2>/dev/null || true); do
  echo "  stopping pid ${pid}"
  kill "${pid}" 2>/dev/null || true
done
sleep 1

if [[ ! -f "${PROD_CONFIG}" ]]; then
  echo "Production config not found: ${PROD_CONFIG}" >&2
  exit 1
fi
if [[ ! -x "${XRAY_BIN}" ]]; then
  echo "Xray binary not found: ${XRAY_BIN}" >&2
  exit 1
fi

mkdir -p "${PROBE_CONFIG_DIR}"

python3 - "${PROD_CONFIG}" "${PROBE_CONFIG}" "${SOCKS_DE_PORT}" "${SOCKS_USA_PORT}" <<'PY'
import json
import sys

prod_path, out_path, de_port_s, usa_port_s = sys.argv[1:5]
de_port = int(de_port_s)
usa_port = int(usa_port_s)

prod = json.load(open(prod_path, encoding="utf-8"))
out_by_tag = {o.get("tag"): o for o in prod.get("outbounds", []) if o.get("tag")}
germany = out_by_tag.get("germany")
usa = out_by_tag.get("usa")
if not germany or not usa:
    raise SystemExit("production config missing germany/usa outbounds")

direct = {"tag": "direct", "protocol": "freedom", "settings": {}}
blocked = {"tag": "blocked", "protocol": "blackhole", "settings": {}}

de_rules = [
    {"type": "field", "network": "udp,tcp", "port": "53", "outboundTag": "direct"},
    {"type": "field", "ip": ["77.88.8.8", "8.8.8.8", "1.1.1.1", "8.8.4.4"], "outboundTag": "direct"},
    {"type": "field", "domain": ["regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn--p1ai$"], "outboundTag": "direct"},
    {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
    {"type": "field", "network": "tcp,udp", "outboundTag": "germany"},
]
usa_rules = [{"type": "field", "network": "tcp,udp", "outboundTag": "usa"}]

cfg_de = {
    "log": {"loglevel": "warning"},
    "dns": {"servers": ["77.88.8.8", "8.8.8.8"], "queryStrategy": "UseIPv4"},
    "inbounds": [{"listen": "127.0.0.1", "port": de_port, "protocol": "socks", "tag": "probe-de", "settings": {"udp": True}}],
    "outbounds": [direct, blocked, json.loads(json.dumps(germany)), json.loads(json.dumps(usa))],
    "routing": {"domainStrategy": "IPIfNonMatch", "rules": de_rules},
}
cfg_usa = {
    "log": {"loglevel": "warning"},
    "inbounds": [{"listen": "127.0.0.1", "port": usa_port, "protocol": "socks", "tag": "probe-usa", "settings": {"udp": True}}],
    "outbounds": [direct, blocked, json.loads(json.dumps(usa))],
    "routing": {"domainStrategy": "AsIs", "rules": usa_rules},
}

# Single config with both SOCKS inbounds and shared outbounds
merged = {
    "log": {"loglevel": "warning"},
    "dns": {"servers": ["77.88.8.8", "8.8.8.8"], "queryStrategy": "UseIPv4"},
    "inbounds": [
        {"listen": "127.0.0.1", "port": de_port, "protocol": "socks", "tag": "probe-de", "settings": {"udp": True}},
        {"listen": "127.0.0.1", "port": usa_port, "protocol": "socks", "tag": "probe-usa", "settings": {"udp": True}},
    ],
    "outbounds": [direct, blocked, json.loads(json.dumps(germany)), json.loads(json.dumps(usa))],
    "routing": {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {"type": "field", "inboundTag": ["probe-usa"], "outboundTag": "usa"},
            {"type": "field", "network": "udp,tcp", "port": "53", "outboundTag": "direct"},
            {"type": "field", "ip": ["77.88.8.8", "8.8.8.8", "1.1.1.1"], "outboundTag": "direct"},
            {"type": "field", "domain": ["regexp:.*\\.ru$", "regexp:.*\\.su$"], "outboundTag": "direct"},
            {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
            {"type": "field", "inboundTag": ["probe-de"], "outboundTag": "germany"},
        ],
    },
}
json.dump(merged, open(out_path, "w", encoding="utf-8"), indent=2)
print(f"Wrote {out_path}")
PY

install -m644 "${REPO_ROOT}/systemd/xray-hop-probe.service" /etc/systemd/system/xray-hop-probe.service

systemctl daemon-reload
systemctl enable --now xray-hop-probe.service
systemctl restart xray-hop-probe.service

echo "=== xray-hop-probe status ==="
systemctl is-active xray-hop-probe.service
ss -tlnp | grep -E ":${SOCKS_DE_PORT}|:${SOCKS_USA_PORT}" || true
echo "Done. Set in /etc/cock-monitor.env:"
echo "  TELEGRAM_PROXY_URL=socks5h://127.0.0.1:${SOCKS_DE_PORT}"
echo "  HOP_PROBE_ENABLE=1"
echo "  HOP_PROBES=germany:socks5h://127.0.0.1:${SOCKS_DE_PORT}:https://api.ipify.org?format=json:144.31.154.44,usa:socks5h://127.0.0.1:${SOCKS_USA_PORT}:https://api.ipify.org?format=json:153.75.246.28"
