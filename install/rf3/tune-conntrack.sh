#!/usr/bin/env bash
# RF3: reduce stale conntrack TCP lifetime (default kernel often 5 days).
set -euo pipefail

CONF=/etc/sysctl.d/99-cock-rf3-conntrack.conf
TIMEOUT="${CONNTRACK_TCP_ESTABLISHED_TIMEOUT:-86400}"

cat >"$CONF" <<EOF
# cock-monitor RF3: shorten established TCP conntrack lifetime (seconds)
net.netfilter.nf_conntrack_tcp_timeout_established=${TIMEOUT}
EOF

sysctl -p "$CONF"
echo "Applied ${CONF} (nf_conntrack_tcp_timeout_established=${TIMEOUT})"
