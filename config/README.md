# cock-monitor v2 configuration

## ENABLED_MODULES

Only listed modules are active (timers, Telegram commands, SQLite schemas):

```bash
ENABLED_MODULES=core,vless,incident,shaper
```

`core` is always included if omitted from explicit list logic — install profiles set it explicitly.

## Fragments

Per-module defaults live in [`fragments/`](fragments/):

| File | Module |
|------|--------|
| `core.env` | conntrack, host metrics, LA/MEM alerts, Telegram |
| `vless.env` | 3x-ui VLESS reports |
| `mtproxy.env` | MTProto proxy |
| `wg.env` | WireGuard peers |
| `incident.env` | incident sampler JSONL |
| `shaper.env` | CPU-aware CAKE shaper |

## Profiles

[`profiles/`](profiles/) combine `ENABLED_MODULES` + host-specific overrides:

| Profile | Modules | Typical hosts |
|---------|---------|---------------|
| `core` | core | minimal |
| `stack-3xui` | core,vless,incident,shaper | NY, Madrid, Germany, London, Helsinki |
| `stack-mtproxy` | core,mtproxy | MTProxy VPS |
| `stack-rf2-wg` | core,wg,incident | RF2 |
| `stack-rf1` | core,incident | RF1 |

Install merges: fragments for each enabled module → profile overrides → `/etc/cock-monitor.env`.

```bash
sudo bash install/install.sh --profile stack-rf2-wg --token '...' --chat-id '...' --wipe-data
```
