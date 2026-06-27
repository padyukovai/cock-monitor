---
name: server-disk-cleanup
description: >-
  Diagnose and safely free disk space on cock-monitor VPS servers (small Ubuntu
  root volumes). Use when disk is full, df shows 100%, users report no space
  left, or periodic maintenance is needed after weeks of uptime.
---

# Server disk cleanup

Safe cleanup runbook for cock-monitor VPS hosts (typical root disk ~5 GB).

## Defaults

```bash
export DEPLOY_HOST=root@<your-server>
```

Run all remote commands via SSH. Always diagnose before deleting.

## Workflow

```
Task progress:
- [ ] Step 1: Measure disk usage
- [ ] Step 2: Identify top consumers
- [ ] Step 3: Run safe cleanup
- [ ] Step 4: Ensure journal limits persist
- [ ] Step 5: Verify services still healthy
- [ ] Step 6: Report freed space and what was removed
```

## Step 1: Measure disk usage

```bash
ssh "$DEPLOY_HOST" "hostname; df -h /; du -xhd1 / 2>/dev/null | sort -h"
```

If `Avail` is 0 or `Use%` is 100%, proceed immediately.

## Step 2: Identify top consumers

```bash
ssh "$DEPLOY_HOST" "du -xhd1 /var 2>/dev/null | sort -h"
ssh "$DEPLOY_HOST" "journalctl --disk-usage"
ssh "$DEPLOY_HOST" "du -sh /var/cache/apt /var/lib/apt/lists /var/lib/cock-monitor /root/.cache 2>/dev/null"
ssh "$DEPLOY_HOST" "ls -lh /var/log/btmp* 2>/dev/null"
```

Typical offenders on cock-monitor VPS:

| Path | Safe to clean? | Typical size |
|------|----------------|--------------|
| `/var/log/journal` | Yes (vacuum) | 400–500 MB |
| `/var/cache/apt`, `/var/lib/apt/lists` | Yes | 300–450 MB |
| `/var/log/btmp`, `/var/log/btmp.1` | Yes (truncate) | 50–120 MB |
| `/var/lib/cock-monitor/incident-*.jsonl` (>14 days) | Yes | up to ~300 MB |
| `/var/log/x-ui/*.prev.log` | Yes (truncate) | ~10–20 MB |
| `/opt/cock-monitor`, `/etc/cock-monitor.env` | **No** | — |
| `/var/lib/dpkg`, running service data | **No** | — |

## Step 3: Safe cleanup

Prefer the bundled script (idempotent, prints before/after):

```bash
ssh "$DEPLOY_HOST" "bash -s" < .cursor/skills/server-disk-cleanup/scripts/cleanup-disk.sh
```

Or run inline if the script is unavailable on the operator machine:

```bash
ssh "$DEPLOY_HOST" "set -e
echo '=== before ==='
df -h /
journalctl --disk-usage
journalctl --vacuum-size=100M
apt-get clean
rm -rf /var/lib/apt/lists/*
: > /var/log/btmp
: > /var/log/btmp.1
find /var/lib/cock-monitor -maxdepth 1 -name 'incident-*.jsonl' -mtime +14 -print -delete
[ -f /var/log/x-ui/3xipl-ap.prev.log ] && : > /var/log/x-ui/3xipl-ap.prev.log
rm -rf /root/.cache/*
echo '=== after ==='
df -h /
journalctl --disk-usage"
```

### Do not delete without explicit approval

- Application databases, env files, TLS certs
- `/opt/cock-monitor/.venv311`
- MTProxy secrets (`/opt/MTProxy/proxy-secret`, `proxy-multi.conf`)
- Anything under `/usr` or `/opt` except known cache/temp

## Step 4: Persist journal size limit

Prevent journal from refilling disk within weeks:

```bash
ssh "$DEPLOY_HOST" "mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-size-limit.conf <<'EOF'
[Journal]
SystemMaxUse=100M
RuntimeMaxUse=50M
EOF
systemctl restart systemd-journald"
```

Skip if `99-size-limit.conf` already exists with the same values.

## Step 5: Verify services

```bash
ssh "$DEPLOY_HOST" "systemctl is-active mtproto x-ui ssh cock-monitor.timer cock-monitor-telegram-bot.timer"
ssh "$DEPLOY_HOST" "ss -lntup | awk 'NR==1 || /:22|:443|:8443/'"
```

If `mtproto` is down, check `kernel.pid_max` (see DEPLOY.md ops note) before blaming cleanup.

## Step 6: Report to user

Use this template:

```markdown
## Disk cleanup on <hostname>

**Before:** <used%> (<avail> free)
**After:** <used%> (<avail> free)

### Removed
- journal vacuum → ~<N> MB
- apt cache/lists → ~<N> MB
- btmp logs → ~<N> MB
- incident logs >14d → <count> files
- other: ...

### Persisted
- journald SystemMaxUse=100M (if applied)

### Health check
- mtproto: <status>
- x-ui: <status>
- cock-monitor timers: <status>
```

## Optional deeper cleanup

Only if still above 90% after safe steps:

```bash
ssh "$DEPLOY_HOST" "apt-get autoremove -y"
ssh "$DEPLOY_HOST" "find /var/log -type f -name '*.gz' -mtime +30 -print"
ssh "$DEPLOY_HOST" "find /var/log -type f -name '*.1' -mtime +30 -print"
```

Review `find` output before deleting rotated logs.

## Related

- MTProxy `pid_max` outage: `DEPLOY.md` section "Ops заметка: MTProxy падает из-за большого pid_max"
- Deploy host defaults: `DEPLOY.md`
