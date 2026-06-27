#!/bin/bash
# Add 5 FakeTLS (ee) MTProxy secrets alongside existing plain (dd) secrets.
# Run on the VPS as root: bash add-faketls-secrets.sh
#
# Env overrides:
#   FAKETLS_DOMAIN=www.google.com   domain for TLS camouflage (-D)
#   MTPROXY_PUBLIC_IP=163.5.41.47  IP in client links
#   MTPROXY_PORT=8443               client port (same as -H)
#   FAKETLS_COUNT=5                 number of secrets to generate
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FAKETLS_DOMAIN="${FAKETLS_DOMAIN:-www.google.com}"
MTPROXY_PUBLIC_IP="${MTPROXY_PUBLIC_IP:-163.5.41.47}"
MTPROXY_PORT="${MTPROXY_PORT:-8443}"
FAKETLS_COUNT="${FAKETLS_COUNT:-5}"

ETC_DIR="/etc/mtproto"
FAKETLS_SECRETS="${ETC_DIR}/server-secrets-faketls.txt"
DOMAIN_CONF="${ETC_DIR}/faketls-domain.conf"
LINKS_OUT="${ETC_DIR}/client-links-faketls.txt"
RUN_SCRIPT="/usr/local/bin/mtproto-run.sh"
SHOW_LINKS="/usr/local/bin/mtproto-show-faketls-links.sh"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

mkdir -p "$ETC_DIR"
chmod 700 "$ETC_DIR"

if [[ -f "$FAKETLS_SECRETS" ]] && grep -q . "$FAKETLS_SECRETS" 2>/dev/null; then
  echo "FakeTLS secrets already exist at $FAKETLS_SECRETS"
  echo "Delete the file or rename it to regenerate."
  exit 1
fi

if [[ ! -f "${ETC_DIR}/server-secrets.txt" ]]; then
  echo "Missing ${ETC_DIR}/server-secrets.txt (plain secrets)." >&2
  exit 1
fi

if [[ ! -x /opt/MTProxy/objs/bin/mtproto-proxy ]]; then
  echo "mtproto-proxy binary not found at /opt/MTProxy/objs/bin/mtproto-proxy" >&2
  exit 1
fi

echo "Generating ${FAKETLS_COUNT} FakeTLS secrets (domain: ${FAKETLS_DOMAIN})..."
: > "$FAKETLS_SECRETS"
for i in $(seq 1 "$FAKETLS_COUNT"); do
  hex="$(openssl rand -hex 16)"
  echo "faketls-${i}:${hex}" >> "$FAKETLS_SECRETS"
done
chmod 600 "$FAKETLS_SECRETS"

printf '%s\n' "$FAKETLS_DOMAIN" > "$DOMAIN_CONF"
chmod 600 "$DOMAIN_CONF"

if [[ -f "$RUN_SCRIPT" ]]; then
  cp -a "$RUN_SCRIPT" "${RUN_SCRIPT}.bak.$(date +%Y%m%d%H%M%S)"
fi
install -m755 "${SCRIPT_DIR}/mtproto-run.sh" "$RUN_SCRIPT"
install -m755 "${SCRIPT_DIR}/mtproto-show-faketls-links.sh" "$SHOW_LINKS"

echo "Restarting mtproto.service..."
systemctl restart mtproto.service
sleep 2
if ! systemctl is-active --quiet mtproto.service; then
  echo "mtproto.service failed to start. Check: journalctl -u mtproto.service -n 50" >&2
  exit 1
fi

echo
echo "OK. Plain (dd) secrets unchanged; FakeTLS (ee) added on port ${MTPROXY_PORT}."
echo "Saved links: ${LINKS_OUT}"
echo

"$SHOW_LINKS" "$MTPROXY_PUBLIC_IP" "$MTPROXY_PORT" | tee "$LINKS_OUT"
chmod 600 "$LINKS_OUT"

echo
echo "Verify process args:"
pgrep -af mtproto-proxy || true
