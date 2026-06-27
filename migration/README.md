# 3x-ui client migration (cockvpn.org → cock-is)

Restores VLESS clients from the cockvpn.org backup onto the USA VPS running 3x-ui 3.3.0
(native systemd install). SOCKS inbound is skipped by default (not supported by 3.3 API).

## Prerequisites

- Backup archive: `backups/cockvpn-backup-20260608T043354Z.tar.gz`
- Panel credentials via environment variables (never commit passwords):

```bash
export XUI_PANEL_URL='https://153.75.246.28:25241/dungeonmaster'
export XUI_USERNAME='tuhlom'
export XUI_PASSWORD='...'
export XUI_SSH_HOST='cock-is'
```

## Workflow

```bash
# 1) Extract manifest from backup (writes migration/manifest.json — gitignored)
python3 migration/restore_3xui_clients.py extract

# 2) Inspect target panel state
python3 migration/restore_3xui_clients.py dry-run

# 3) Backup remote x-ui.db, create VLESS inbound, import clients via DB (SOCKS skipped)
#    Uses direct SQLite import because 3x-ui 3.3 /clients/add returns HTTP 500.
python3 migration/restore_3xui_clients.py apply

# 4) Verify VLESS link params for sample clients + listening ports
python3 migration/restore_3xui_clients.py verify
```

## Post-migration checks

```bash
# VLESS port
ssh cock-is 'ss -tlnp | grep :443'
```

## Rollback

```bash
ssh cock-is 'x-ui stop && cp -a /etc/x-ui/x-ui.db.pre-migration-* /etc/x-ui/x-ui.db && x-ui start'
```

Use the newest `x-ui.db.pre-migration-*` file created by the `apply` step.

## Notes

- Client UUIDs and Reality keys are copied from the backup so existing client configs keep working after DNS points to the new server.
- Traffic counters are **not** restored (fresh start).
- `migration/manifest.json` contains private keys — kept out of git via `.gitignore`.
