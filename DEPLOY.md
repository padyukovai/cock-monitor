# Деплой на сервер (актуальный runbook)

Короткая и рабочая инструкция для обновления уже установленного сервиса.
Для первичной установки на чистую Ubuntu: `sudo bash install/install.sh --profile <name>`.

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

### v2: профили и daily timers

Чистая установка по роли VPS (см. [`install/profiles.md`](install/profiles.md)):

```bash
sudo bash install/install.sh --profile stack-exit-node --token "$TELEGRAM_BOT_TOKEN" --chat-id "$TELEGRAM_CHAT_ID"
# Helsinki: --profile stack-mtproxy
# RF3:      --profile stack-rf3
```

`install` ставит modular timers **и** daily-отчёты по включённым модулям:

- `core` → `cock-monitor-daily.timer` (PNG chart)
- `vless` → `cock-vless-daily.timer`
- `mtproxy` → `cock-mtproxy-daily.timer`

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

- Если изменились только файлы в `cock_monitor/`, `bin/`, `lib/` — чаще всего достаточно `git pull`.
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
ssh "$DEPLOY_HOST" "cd $APP_DIR && sudo bash install/install.sh --profile stack-exit-node --token \"\$TELEGRAM_BOT_TOKEN\" --chat-id \"\$TELEGRAM_CHAT_ID\""
```

Или переустановить только systemd-шаблоны v2 вручную:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && sudo cp systemd/cock-monitor-*.service systemd/cock-monitor-*.timer \
  systemd/cock-*-daily.* /etc/systemd/system/ 2>/dev/null; sudo systemctl daemon-reload"
```

## Проверка Python runtime перед smoke и запуском unit'ов

Начиная с текущих релизов, части кода могут требовать Python 3.11+.
Перед smoke-проверкой удобно определить, какой интерпретатор использовать на сервере.

```bash
export PYTHON_BIN="$(ssh "$DEPLOY_HOST" "set -e
if python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo python3
elif [ -x \"$APP_DIR/.venv311/bin/python\" ]; then
  echo \"$APP_DIR/.venv311/bin/python\"
else
  echo 'ERROR: Python 3.11+ is required. Neither system python3>=3.11 nor $APP_DIR/.venv311/bin/python found.' >&2
  exit 1
fi")"

echo "Using interpreter: $PYTHON_BIN"
```

Примечание: если выбран `.venv311`, для `systemd` unit-файлов нужно использовать тот же интерпретатор в `ExecStart`.

## Smoke после деплоя

Минимальный набор:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && $PYTHON_BIN -m cock_monitor preflight $ENV_FILE"
ssh "$DEPLOY_HOST" "cd $APP_DIR && $PYTHON_BIN -m cock_monitor conntrack-check --dry-run $ENV_FILE"
ssh "$DEPLOY_HOST" "cd $APP_DIR && COCK_MONITOR_HOME=$APP_DIR $PYTHON_BIN -m cock_monitor.platform.telegram --poll-once $ENV_FILE"
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

## Ops заметка: MTProxy падает из-за большого `pid_max`

Симптомы:

- клиенты жалуются, что MTProxy недоступен;
- `mtproto.service` в рестартах;
- в логах `mtproto-proxy`: `init_common_PID: Assertion '!(p & 0xffff0000)' failed`.

Быстрая диагностика:

```bash
ssh "$DEPLOY_HOST" "systemctl --no-pager --full status mtproto.service | sed -n '1,40p'"
ssh "$DEPLOY_HOST" "journalctl -u mtproto.service -n 80 --no-pager"
ssh "$DEPLOY_HOST" "sysctl kernel.pid_max"
```

Причина: текущий бинарь `mtproto-proxy` не работает с PID > `65535`, а на части систем дефолт `kernel.pid_max` может быть существенно выше.

Безопасный фикс:

```bash
ssh "$DEPLOY_HOST" "sysctl -w kernel.pid_max=65535"
ssh "$DEPLOY_HOST" "printf '%s\n' 'kernel.pid_max = 65535' > /etc/sysctl.d/99-mtproxy-pid-max.conf"
ssh "$DEPLOY_HOST" "sysctl --system"
ssh "$DEPLOY_HOST" "systemctl restart mtproto.service"
```

Проверка после фикса:

```bash
ssh "$DEPLOY_HOST" "systemctl --no-pager --full status mtproto.service | sed -n '1,40p'"
ssh "$DEPLOY_HOST" "ss -lntup | awk 'NR==1 || /:8443|mtproto/'"
```

Примечание: если `sysctl --system` печатает предупреждения по ключам из `/usr/lib/sysctl.d/50-default.conf` (`accept_source_route`/`promote_secondaries`), не редактируй файл в `/usr/lib`. Делай override в `/etc/sysctl.d/`.

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
