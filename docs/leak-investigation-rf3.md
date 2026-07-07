# RF3 leak investigation — 24h validation protocol

Use this runbook on `cock-rf3` after deploying leak diagnostics changes.

## Prerequisites

- `ENABLED_MODULES` includes `core` and `incident`
- `LEAK_PROBE_ENABLE=1` in `/etc/cock-monitor.env`
- `cock-monitor-core.timer` active (5 min baseline)
- Optional trend alerts: `LEAK_ALERT_ENABLE=1`

## Start 24h window

```bash
ssh cock-rf3 '
  cd /opt/cock-monitor && git pull --ff-only
  .venv/bin/python -m cock_monitor leak-investigation start --hours 24 --env-file /etc/cock-monitor.env
  systemctl enable --now cock-monitor-leak-investigation.timer
  systemctl status cock-monitor-leak-investigation.timer --no-pager
'
```

Enriched samples land in `/var/lib/cock-monitor/leak-investigation-YYYYMMDD.jsonl` (60s).

## During the window

Monitor via Telegram:

- `/chart` — classic conntrack + MemAvailable
- `/chart leak` — xray RSS, FDs, socket states, conntrack fill

Or on host:

```bash
sqlite3 /var/lib/cock-monitor/metrics.db \
  "SELECT datetime(ts,'unixepoch'), xray_rss_mb, mem_avail_kb/1024, ss_time_wait
   FROM host_samples ORDER BY id DESC LIMIT 20;"
```

## End of window

Auto-report fires when `INCIDENT_LEAK_AUTO_REPORT=1` (Telegram HTML).

Manual report:

```bash
.venv/bin/python -m cock_monitor leak-investigation report --env-file /etc/cock-monitor.env --send-telegram
systemctl disable --now cock-monitor-leak-investigation.timer
```

## Hypothesis criteria

| Hypothesis | Confirmed if | Rejected if |
|------------|--------------|-------------|
| **xray memory leak (primary)** | RSS grows ≥50 MB over 6h with r>0.5 vs time; MemAvailable falls in sync (r>0.65); restart at 04:00 drops RSS sharply | RSS flat (<20 MB delta) while MemAvailable still falls |
| **conntrack accumulation (primary)** | conntrack fill/count rises overnight without client load; weak RSS correlation | conntrack high but RSS flat; conntrack drops when clients drop |
| **TIME-WAIT / proxy churn** | TIME-WAIT grows >1000 with high 8080/443 peer ports in enriched samples | TIME-WAIT stable overnight |
| **Mixed xray + conntrack** | Both RSS and conntrack correlate (r>0.5) but RSS leads conntrack by ≥1 core tick | Only one series trends |

## Decision tree

1. If xray RSS trend confirmed → prioritize xray version/config investigation before sysctl tuning.
2. If conntrack-only → review `nf_conntrack_tcp_timeout_established`, local proxy loops (127.0.0.1:8080).
3. If inconclusive → extend investigation 24h or run `burst-capture` during peak load.

## Rollback

```bash
systemctl disable --now cock-monitor-leak-investigation.timer
.venv/bin/python -m cock_monitor leak-investigation stop --env-file /etc/cock-monitor.env
```

Core leak metrics remain in `host_samples` (low overhead); disable with `LEAK_PROBE_ENABLE=0`.
