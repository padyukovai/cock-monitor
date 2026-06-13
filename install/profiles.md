# Server → profile matrix

| Host | SSH | Profile |
|------|-----|---------|
| NY (cock-is) | `cock-is` | `stack-3xui` |
| Madrid | `root@83.147.242.226` | `stack-3xui` (+ `mtproxy` if used) |
| Germany | `cock-germany` | `stack-3xui` |
| London | `cock-london` | `stack-3xui` |
| Helsinki | `cock-helsinki` | `stack-3xui` |
| RF1 | `whitelisthack` | `stack-rf1` |
| RF2 | `rf2` | `stack-rf2-wg` |

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
```

Verify:

```bash
systemctl list-timers 'cock-monitor-*'
sudo .venv/bin/python -m cock_monitor modules enabled /etc/cock-monitor.env
sudo .venv/bin/python -m cock_monitor run core /etc/cock-monitor.env --dry-run
```

Telegram: `/help` shows only commands for enabled modules.
