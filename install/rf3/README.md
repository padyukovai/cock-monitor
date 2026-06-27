# RF3 (hop-gateway) post-install

Profile `stack-rf3` installs cock-monitor modules (`core`, `hop`, `incident`).  
Hop SOCKS probes and Telegram egress via proxy are **not** part of modular install.

## After `install.sh --profile stack-rf3`

Install prints a checklist. Run manually:

```bash
sudo bash install/rf3/setup-hop-probe.sh
```

This creates `xray-hop-probe.service` (local SOCKS `10891`/`10892`) and enables hop egress probes.

Optional: pass `PUBLIC_IP` if auto-detect fails.

## Verify

```bash
sudo .venv/bin/python -m cock_monitor preflight --profile stack-rf3 /etc/cock-monitor.env
systemctl status xray-hop-probe.service
sudo .venv/bin/python -m cock_monitor run hop /etc/cock-monitor.env --dry-run
```

## Automated post-install

```bash
sudo bash install/install.sh --profile stack-rf3 --token '...' --chat-id '...' --run-post-install
```

`--run-post-install` executes `POST_INSTALL_SCRIPTS` from the profile. Default install only prints the checklist.
