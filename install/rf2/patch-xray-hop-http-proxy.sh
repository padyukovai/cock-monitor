#!/usr/bin/env bash
# Add local HTTP proxy on 127.0.0.1:10809 → VLESS Germany (de-exit) for cock-monitor Telegram.
set -euo pipefail

CONFIG="${XRAY_HOP_CONFIG:-/etc/xray-rf2-hop/config.json}"
HTTP_PORT="${TELEGRAM_LOCAL_HTTP_PROXY_PORT:-10809}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run: sudo bash install/rf2/patch-xray-hop-http-proxy.sh" >&2
  exit 1
fi

if [[ ! -f "${CONFIG}" ]]; then
  echo "Config not found: ${CONFIG}" >&2
  exit 1
fi

python3 - "${CONFIG}" "${HTTP_PORT}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
port = int(sys.argv[2])
cfg = json.loads(path.read_text(encoding="utf-8"))

inbounds = cfg.setdefault("inbounds", [])
if not any(ib.get("tag") == "local-http" for ib in inbounds):
    inbounds.append(
        {
            "tag": "local-http",
            "listen": "127.0.0.1",
            "port": port,
            "protocol": "http",
            "settings": {},
            "sniffing": {"enabled": False},
        }
    )

rules = cfg.setdefault("routing", {}).setdefault("rules", [])
if not any(r.get("inboundTag") == ["local-http"] for r in rules):
    rules.append(
        {
            "type": "field",
            "inboundTag": ["local-http"],
            "outboundTag": "de-exit",
        }
    )

path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print(f"OK: local HTTP proxy 127.0.0.1:{port} -> de-exit in {path}")
PY

systemctl restart xray-rf2-hop.service
echo "Restarted xray-rf2-hop.service"
echo "Test: curl -x http://127.0.0.1:${HTTP_PORT} -sS -o /dev/null -w '%{http_code}\\n' --connect-timeout 10 https://api.telegram.org/"
