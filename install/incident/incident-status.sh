#!/bin/bash
# Human-readable summary of incident sampler JSONL (SSH-friendly).
set -euo pipefail

LOG_DIR="${INCIDENT_LOG_DIR:-/var/lib/cock-monitor}"
LAST=10
DAY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --last) LAST="${2:-10}"; shift 2 ;;
    --day) DAY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: incident-status [--last N] [--day YYYYMMDD]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$DAY" ]] || DAY="$(date -u +%Y%m%d)"
FILE="${LOG_DIR}/incident-${DAY}.jsonl"

if [[ ! -f "$FILE" ]]; then
  echo "No log: $FILE"
  exit 1
fi

export FILE LAST
python3 <<'PY'
import json
import os

path = os.environ["FILE"]
last = int(os.environ.get("LAST", "10"))
rows = []
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
if not rows:
    print("(empty)")
    raise SystemExit(0)

warn = sum(1 for r in rows if r.get("level") in ("WARN", "CRIT"))
print(f"file={path} samples={len(rows)} warn_crit={warn}")
print()
print(f"{'time':<20} {'lvl':<4} {'ct%':>4} {'tcp':>12} {'probe_fail':>10} {'units':<30}")
print("-" * 90)

def probe_summary(r):
    tp = r.get("tcp_probe") or {}
    if not tp.get("enabled"):
        return "-"
    t = tp.get("totals", {}).get("all", {})
    return f"{t.get('fails', 0)}/{t.get('total', 0)}"

def units_summary(r):
    u = r.get("units") or {}
    parts = []
    for name in ("mtproto.service", "ssh.service", "x-ui.service"):
        short = name.replace(".service", "")
        parts.append(f"{short}={u.get(name, '?')}")
    return " ".join(parts)

for r in rows[-last:]:
    ts = (r.get("ts") or "")[11:19]
    lvl = r.get("level", "?")
    ct = r.get("conntrack", {})
    ct_pct = ct.get("fill_pct", 0)
    tcp = r.get("tcp", {})
    tcp_s = f"e={tcp.get('estab', 0)} s={tcp.get('syn_recv', 0)} tw={tcp.get('time_wait', 0)}"
    print(f"{ts:<20} {lvl:<4} {ct_pct:>4} {tcp_s:>12} {probe_summary(r):>10} {units_summary(r):<30}")

print()
print("TCP probe detail (last sample):")
last = rows[-1]
for chk in (last.get("tcp_probe") or {}).get("checks") or []:
    ok = "OK" if chk.get("ok") else "FAIL"
    print(
        f"  {chk.get('scope')} {chk.get('target')}:{chk.get('port')} "
        f"{ok} {chk.get('latency_ms')}ms {chk.get('error') or ''}"
    )
PY
