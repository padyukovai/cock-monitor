# Server → profile matrix

## Role presets (`install --role <name>`)

| Role | Profile | Modules | Typical VPS |
|------|---------|---------|-------------|
| `hop-gateway` | `stack-rf3` | core, hop, incident, vless | RF3 |
| `exit-node` | `stack-exit-node` | core, vless, incident, shaper | Germany, USA, London |
| `mtproxy-only` | `stack-mtproxy` | core, mtproxy | Helsinki |
| `wg-relay` | `stack-rf2-wg` | core, wg, incident | RF2 |
| `minimal` | `stack-rf1` | core, incident | RF1 |

`install --role mtproxy-only` is equivalent to `--profile stack-mtproxy`.

## Host → profile matrix

| Host | SSH | Profile | Modules |
|------|-----|---------|---------|
| NY (cock-is) | `cock-is` | `stack-exit-node` or `stack-3xui` | core, vless, incident, shaper |
| Madrid | `root@83.147.242.226` | `stack-exit-node` (+ `mtproxy` if used) | |
| Germany | `cock-germany` | `stack-exit-node` or `stack-3xui` | |
| London | `cock-london` | `stack-exit-node` or `stack-3xui` | |
| Helsinki | `cock-helsinki` | `stack-mtproxy` | core, mtproxy |
| RF1 | `whitelisthack` | `stack-rf1` | core, incident |
| RF2 | `rf2` | `stack-rf2-wg` | core, wg, incident |
| RF3 | `cock-rf3` | `stack-rf3` | core, incident, hop, vless |

`stack-exit-node` is an alias of `stack-3xui` (readable name for DE/US exit nodes).

Daily timers installed automatically by profile:

| Profile | Daily timers |
|---------|----------------|
| `stack-3xui` / `stack-exit-node` | `cock-monitor-daily`, `cock-vless-daily` |
| `stack-mtproxy` | `cock-monitor-daily`, `cock-mtproxy-daily` |
| `stack-rf3` | `cock-monitor-daily`, `cock-vless-daily` |

## Clean redeploy (breaking v2)

On each server from the **cock-monitor git clone**:

```bash
cd /opt/cock-monitor   # or your clone path
git pull
sudo bash install/uninstall.sh --wipe-data
sudo bash install/install.sh \
  --profile stack-3xui \
  --token "$TELEGRAM_BOT_TOKEN" \
  --chat-id "$TELEGRAM_CHAT_ID" \
  --wipe-data
```

RF2 example:

```bash
sudo bash install/install.sh --profile stack-rf2-wg --token '...' --chat-id '...' --wipe-data
sudo bash install/rf2/patch-xray-hop-http-proxy.sh   # if not using --run-post-install
```

Helsinki (MTProxy only):

```bash
sudo bash install/install.sh --profile stack-mtproxy --token '...' --chat-id '...' --wipe-data
# then: sudo bash install/mtproto/restore-mtproxy.sh (see install checklist)
```

Germany / USA (exit-node):

```bash
sudo bash install/install.sh --profile stack-exit-node --token '...' --chat-id '...' --wipe-data
```

RF3 example (hop link monitoring to Germany / USA exits):

```bash
sudo bash install/install.sh --profile stack-rf3 --token '...' --chat-id '...' --wipe-data
# install prints post-install checklist; then:
sudo bash install/rf3/setup-hop-probe.sh   # creates xray-hop-probe.service
# or: add --run-post-install to install.sh to run profile scripts automatically
```

See [install/rf3/README.md](rf3/README.md).

On Germany (optional hop inbound monitoring without hop module), add to `/etc/cock-monitor.env`:

```bash
HOP_LINKS=rf3-de:sport::10089
INCIDENT_HOP_ESTAB_WARN=5
INCIDENT_HOP_FIN_WAIT_WARN=20
```

Hop Telegram alerts on RF3 are owned by the **hop** module (`HOP_*` thresholds). With `hop` in `ENABLED_MODULES`, incident still writes `hop_links` to JSONL but does not escalate WARN/CRIT on hop metrics.

**RF3 minimal stack** (`core,hop` only) drops JSONL post-mortem; default profile keeps `incident` for diagnostics.

Verify:

```bash
systemctl list-timers 'cock-monitor-*' 'cock-vless-daily.timer' 'cock-mtproxy-daily.timer'
sudo .venv/bin/python -m cock_monitor modules enabled /etc/cock-monitor.env
sudo .venv/bin/python -m cock_monitor run core /etc/cock-monitor.env --dry-run
```

Telegram: `/help` shows only commands for enabled modules.

## Profile ops metadata

Profiles may declare ops keys (not written to `/etc/cock-monitor.env`):

| Key | Purpose |
|-----|---------|
| `POST_INSTALL_SCRIPTS` | Manual steps printed after install (`--run-post-install` to execute) |
| `PREFLIGHT_SYSTEMD_UNITS` | Units checked by `preflight --profile <name>` |
| `PREFLIGHT_TCP_PORTS` | Local listen ports checked by preflight |

```bash
sudo .venv/bin/python -m cock_monitor preflight --profile stack-rf3 /etc/cock-monitor.env
```

## Telegram token (interactive)

After install, set bot token and chat id on the server (safe for special characters in token):

```bash
cd /opt/cock-monitor
sudo bash install/set-telegram-credentials.sh
```

The script asks for `TELEGRAM_BOT_TOKEN` (hidden input) and `TELEGRAM_CHAT_ID`, backs up `/etc/cock-monitor.env`, and starts `cock-monitor-telegram.service`.

## RF2: Telegram via VLESS Germany

RF2 blocks direct access to `api.telegram.org`. The hop `xray-rf2-hop` exits via VLESS to Germany (`144.31.154.44`).

1. Enable local HTTP proxy on the hop (once per server):

```bash
sudo bash install/rf2/patch-xray-hop-http-proxy.sh
```

2. Profile `stack-rf2-wg` sets `TELEGRAM_PROXY_URL=http://127.0.0.1:10809`. After install or env change:

```bash
grep TELEGRAM_PROXY_URL /etc/cock-monitor.env
sudo systemctl start cock-monitor-telegram.service
```
