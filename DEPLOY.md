# Деплой на сервер (актуальный runbook)

Короткая и рабочая инструкция для обновления уже установленного сервиса.

## Значения по умолчанию

- Сервер: `root@<your-server>`
- Папка установки на сервере: `/opt/cock-monitor`
- Боевой env-файл: `/etc/cock-monitor.env`

Рекомендуется задать:

```bash
export DEPLOY_HOST=root@<your-server>
export APP_DIR=/opt/cock-monitor
export ENV_FILE=/etc/cock-monitor.env
```

## Стандартный деплой (git pull)

1) Проверить, что целевая папка и репозиторий на месте:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git rev-parse --short HEAD && git status --short --branch"
```

2) Обновить код:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git pull --ff-only"
```

3) Понять, что именно приехало в этом деплое:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git log -2 --oneline"
ssh "$DEPLOY_HOST" "cd $APP_DIR && git show --name-only --oneline --pretty=medium HEAD"
```

## Проверка: нужны ли правки в конфиге или таймерах

После `pull` обязательно определить, требует ли релиз действий вне `git`.

### Быстрые правила

- Если изменились только файлы в `cock_monitor/`, `telegram_bot/`, `bin/`, `lib/` — чаще всего ничего не нужно.
- Если изменились файлы в `systemd/` — нужно переустановить unit-файлы и сделать `daemon-reload`.
- Если изменились `config.example.env` или `config.minimal.env` — нужно сверить и при необходимости дописать новые переменные в `/etc/cock-monitor.env`.

### Что проверить командами

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git diff --name-only HEAD~1..HEAD"
```

Опционально, если в релизе несколько коммитов:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git diff --name-only ORIG_HEAD..HEAD"
```

## Применение systemd-изменений (только если менялся `systemd/`)

```bash
ssh "$DEPLOY_HOST" "install -m644 \
  $APP_DIR/systemd/cock-monitor.service \
  $APP_DIR/systemd/cock-monitor.timer \
  $APP_DIR/systemd/cock-monitor-telegram-bot.service \
  $APP_DIR/systemd/cock-monitor-telegram-bot.timer \
  $APP_DIR/systemd/cock-monitor-daily.service \
  $APP_DIR/systemd/cock-monitor-daily.timer \
  $APP_DIR/systemd/cock-mtproxy-monitor.service \
  $APP_DIR/systemd/cock-mtproxy-monitor.timer \
  $APP_DIR/systemd/cock-mtproxy-daily.service \
  $APP_DIR/systemd/cock-mtproxy-daily.timer \
  $APP_DIR/systemd/cock-shaper.service \
  $APP_DIR/systemd/cock-shaper.timer \
  $APP_DIR/systemd/cock-monitor-incident-sampler.service \
  $APP_DIR/systemd/cock-monitor-incident-sampler.timer \
  $APP_DIR/systemd/cock-vless-daily.service \
  $APP_DIR/systemd/cock-vless-daily.timer \
  /etc/systemd/system/ && systemctl daemon-reload"
```

## Smoke после деплоя

Минимальный набор:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && python3 -m cock_monitor preflight $ENV_FILE"
ssh "$DEPLOY_HOST" "cd $APP_DIR && python3 -m cock_monitor conntrack-check --dry-run $ENV_FILE"
ssh "$DEPLOY_HOST" "cd $APP_DIR && COCK_MONITOR_HOME=$APP_DIR python3 -m telegram_bot --poll-once $ENV_FILE"
```

Проверка таймеров:

```bash
ssh "$DEPLOY_HOST" "systemctl list-timers --all | awk 'NR==1 || /cock|telegram|mtproxy/'"
```

## Частые post-deploy действия

- Обновились команды Telegram-бота: запусти `cock-monitor-telegram-bot.service`, чтобы меню применилось сразу:

```bash
ssh "$DEPLOY_HOST" "systemctl start cock-monitor-telegram-bot.service && systemctl status cock-monitor-telegram-bot.service --no-pager"
```

- Изменился только код без `systemd/` и env-контракта: обычно достаточно `git pull` + smoke.

## Rollback (быстро)

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git reset --hard HEAD~1"
ssh "$DEPLOY_HOST" "systemctl daemon-reload"
ssh "$DEPLOY_HOST" "systemctl restart cock-monitor.timer"
```

Если откатили релиз с изменениями в env, верни резервную копию `/etc/cock-monitor.env`.

## Важно

- Секреты хранятся только в `/etc/cock-monitor.env`, не в репозитории.
- Не делай деплой при грязном рабочем дереве на сервере, пока не понятно происхождение локальных правок.
- Полные детали по установке и подсистемам смотри в `README.md`.
