#!/bin/bash
# FakeTLS MTProxy, direct-to-DC (GetPageSpeed fork).
set -euo pipefail

BINARY="${MTPROXY_BINARY:-/usr/local/bin/mtproto-proxy}"
CLIENT_PORTS="${MTPROXY_CLIENT_PORTS:-443,8443}"

SECRETS=()
while IFS=: read -r _label hex; do
  [[ -n "${hex:-}" ]] && SECRETS+=(-S "$hex")
done < /etc/mtproto/server-secrets-faketls.txt

domain="$(tr -d '[:space:]' < /etc/mtproto/faketls-domain.conf)"
[[ -n "$domain" ]] || { echo "Missing /etc/mtproto/faketls-domain.conf" >&2; exit 1; }
[[ ${#SECRETS[@]} -gt 0 ]] || { echo "No FakeTLS secrets" >&2; exit 1; }

exec "$BINARY" \
  -u nobody -p 8888 -H "${CLIENT_PORTS}" "${SECRETS[@]}" \
  -D "$domain" --direct \
  --aes-pwd /etc/mtproto/proxy-secret \
  -M 0 --cpu-threads 1 --io-threads 2 \
  -c 10000 --max-accept-rate 300 --nice 5
