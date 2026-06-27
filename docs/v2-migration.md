# cock-monitor v2 migration (breaking)

v2 replaces monolithic env flags and legacy systemd units with **ENABLED_MODULES** + modular timers.

## What changed

- Config: `ENABLED_MODULES=core,vless,...` is the **only** switch for optional modules.
- Legacy flags `MTPROXY_ENABLE`, `INCIDENT_SAMPLER_ENABLE`, `SHAPER_ENABLE` are **deprecated** (still honored with a stderr warning during transition; remove them from env after migrate).
- systemd: `cock-monitor-<module>.timer` instead of `cock-monitor.service`, `cock-mtproxy-*`, etc.
- SQLite: fresh `metrics.db` (no migration from v1)
- CLI: `python -m cock_monitor run <module> /etc/cock-monitor.env`
- Telegram: `python -m cock_monitor.platform.telegram --poll-once /etc/cock-monitor.env`

## Upgrade steps

1. Pull v2 code on the server.
2. Save Telegram token/chat id from old `/etc/cock-monitor.env`.
3. Run uninstall + install (see [install/profiles.md](../install/profiles.md)).

```bash
sudo bash install/uninstall.sh --wipe-data
sudo bash install/install.sh --profile <name> --token ... --chat-id ... --wipe-data
```

4. Confirm timers: `systemctl list-timers 'cock-monitor-*'`
5. Old timers (`cock-monitor.timer`, `cock-mtproxy-*`, …) are removed by uninstall.

## Module reference

See [config/README.md](../config/README.md) and [config/fragments/](../config/fragments/).
