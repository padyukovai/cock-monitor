# RF3 post-install (cock-monitor)

Profile `stack-rf3` installs cock-monitor modules (`core`, `hop`, `incident`, `vless`, `entry`).

`entry` watches VLESS accepts by inbound tag and TLS/i/o errors in xray error.log (TSPU signals).

## After `install.sh --profile stack-rf3`

```bash
sudo bash install/rf3/setup-hop-probe.sh
```

Creates `xray-hop-probe.service` (local SOCKS `10891`/`10892`) and enables hop egress probes.

Optional automated post-install:

```bash
sudo bash install/install.sh --profile stack-rf3 --token '...' --chat-id '...' --run-post-install
```

## Verify monitoring

```bash
sudo .venv/bin/python -m cock_monitor preflight --profile stack-rf3 /etc/cock-monitor.env
systemctl status xray-hop-probe.service
sudo .venv/bin/python -m cock_monitor run hop /etc/cock-monitor.env --dry-run
```

## VPN infrastructure (private repo)

RF3 routing, IPv6, split-tunnel, hop mux tuning, IPv4 rotation (FirstByte), and full server inventory live in the private **cockvpn** sibling repository:

- `install/rf3/` — netplan, policy routing, xray patches
- `.cursor/skills/vps-ssh/` — SSH aliases and stack details
- `.cursor/skills/rf3-ip-block-rotation/` — IP block diagnosis and rotation runbook
- `.cursor/skills/server-disk-cleanup/` — disk maintenance

See cockvpn `install/rf3/README.md` for operational runbooks.
