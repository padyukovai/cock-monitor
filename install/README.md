# install/

Каноническая установка cock-monitor v2 — `install/install.sh` (делегирует в `python -m cock_monitor install`).

## Быстрый старт

```bash
cd /opt/cock-monitor
git pull
sudo bash install/install.sh --role exit-node --token '...' --chat-id '...' --wipe-data
```

Роли: см. [`profiles.md`](profiles.md) (`hop-gateway`, `exit-node`, `mtproxy-only`, `wg-relay`, `minimal`).

## Что делает install

- создаёт/обновляет `.venv` и ставит пакет `[chart]`;
- пишет `/etc/cock-monitor.env` из профиля (`ENABLED_MODULES` + фрагменты);
- ставит v2 modular timers + daily timers по модулям;
- выводит post-install checklist (RF3/Helsinki/RF2);
- опционально `--run-post-install` для скриптов профиля.

## Удаление

```bash
sudo bash install/uninstall.sh --wipe-data
```

## Telegram credentials

```bash
sudo bash install/set-telegram-credentials.sh
```

## Preflight / config-check

```bash
sudo .venv/bin/python -m cock_monitor preflight /etc/cock-monitor.env
sudo .venv/bin/python -m cock_monitor config-check /etc/cock-monitor.env
sudo .venv/bin/python -m cock_monitor config-check --profile stack-mtproxy
```

## Подкаталоги

| Path | Назначение |
|------|------------|
| `rf3/` | post-install hop probe (`setup-hop-probe.sh`) |
| `rf2/` | xray HTTP proxy patch для Telegram |
| `mtproto/` | MTProxy restore/stabilize (Helsinki) |
| `incident/` | legacy helper `enable-incident-sampler.sh` → v2 incident timer |
