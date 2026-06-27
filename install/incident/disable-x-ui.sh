#!/bin/bash
# Temporarily stop 3x-ui / xray (VLESS) on VPS. MTProxy unaffected.
# Run as root: bash disable-x-ui.sh
# Re-enable: systemctl enable --now x-ui.service
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if ! systemctl list-unit-files x-ui.service >/dev/null 2>&1; then
  echo "x-ui.service not found." >&2
  exit 1
fi

echo "Before:"
systemctl is-active x-ui 2>&1 || true
pgrep -af xray-linux || pgrep -af xray || echo "(no xray process)"
ss -tlnp | grep -E ':443|:8443' || true

systemctl stop x-ui.service
systemctl disable x-ui.service

echo
echo "After:"
echo "  x-ui active: $(systemctl is-active x-ui 2>&1)"
echo "  x-ui enabled: $(systemctl is-enabled x-ui 2>&1)"
pgrep -af xray-linux || pgrep -af xray || echo "  no xray process"
ss -tlnp | grep -E ':443|:8443' || true
echo "  mtproto: $(systemctl is-active mtproto.service 2>&1)"

echo
echo "Done. VLESS/443 stopped. To restore: systemctl enable --now x-ui.service"
