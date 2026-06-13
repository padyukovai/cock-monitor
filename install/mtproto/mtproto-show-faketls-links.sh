#!/bin/bash
# Print Telegram proxy links for FakeTLS secrets (ee).
set -euo pipefail

SERVER="${1:-163.5.41.47}"
PORT="${2:-8443}"
SECRETS_FILE="${3:-/etc/mtproto/server-secrets-faketls.txt}"
DOMAIN_FILE="${4:-/etc/mtproto/faketls-domain.conf}"

if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "Missing $SECRETS_FILE" >&2
  exit 1
fi
if [[ ! -f "$DOMAIN_FILE" ]]; then
  echo "Missing $DOMAIN_FILE" >&2
  exit 1
fi

domain="$(tr -d '[:space:]' < "$DOMAIN_FILE")"
domain_hex="$(printf '%s' "$domain" | xxd -ps -c 256 | tr -d '\n')"

while IFS=: read -r label hex; do
  [[ -z "${hex:-}" ]] && continue
  ee_secret="ee${hex}${domain_hex}"
  echo "=== ${label} (FakeTLS / ee, domain=${domain}) ==="
  echo "tg://proxy?server=${SERVER}&port=${PORT}&secret=${ee_secret}"
  echo "https://t.me/proxy?server=${SERVER}&port=${PORT}&secret=${ee_secret}"
  echo
done < "$SECRETS_FILE"
