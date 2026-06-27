#!/bin/bash
# Refresh proxy-multi.conf and proxy-secret from core.telegram.org; restart if changed.
set -euo pipefail

curl -fsS --max-time 30 https://core.telegram.org/getProxyConfig -o /tmp/proxy-multi.conf.new
curl -fsS --max-time 30 https://core.telegram.org/getProxySecret -o /tmp/proxy-secret.new

changed=0
if ! cmp -s /tmp/proxy-multi.conf.new /etc/mtproto/proxy-multi.conf 2>/dev/null; then
  cp -a /etc/mtproto/proxy-multi.conf "/etc/mtproto/proxy-multi.conf.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  mv /tmp/proxy-multi.conf.new /etc/mtproto/proxy-multi.conf
  chmod 600 /etc/mtproto/proxy-multi.conf
  changed=1
else
  rm -f /tmp/proxy-multi.conf.new
fi

if ! cmp -s /tmp/proxy-secret.new /etc/mtproto/proxy-secret 2>/dev/null; then
  mv /tmp/proxy-secret.new /etc/mtproto/proxy-secret
  chmod 600 /etc/mtproto/proxy-secret
  changed=1
else
  rm -f /tmp/proxy-secret.new
fi

if [[ "$changed" -eq 1 ]]; then
  systemctl restart mtproto.service
  logger -t mtproto-update "Telegram proxy config updated, mtproto restarted"
fi
