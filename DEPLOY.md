# Деплой и обновление на сервере

Инструкция для выката из локального клона репозитория на Linux-хост по **SSH** (пример: `root@cockvpn.org`). Целевой каталог на сервере: **`/opt/cock-monitor`**. Секреты лежат только в **`/etc/cock-monitor.env`** — в репозиторий не коммитятся.

Полное описание установки, Telegram и переменных окружения — в [README.md](README.md).

## Что нужно локально

- `ssh` и `rsync` (есть в macOS и большинстве Linux).
- Доступ по ключу к пользователю с правами **root** на целевом хосте (или подставьте другого пользователя и пути с `sudo`).

Задайте хост один раз в переменной (удобно копировать команды):

```bash
export DEPLOY_HOST=root@cockvpn.org
```

Путь к корню репозитория на вашей машине (при необходимости поправьте):

```bash
export REPO_ROOT="$HOME/MyProjects/cock-monitor"
```

## Первичный деплой (с нуля)

На сервере должны существовать каталоги и конфиг; проще всего выполнить с локальной машины:

```bash
rsync -avz "$REPO_ROOT/bin/" "$DEPLOY_HOST:/tmp/cock-monitor-staging/"
rsync -avz "$REPO_ROOT/lib/" "$DEPLOY_HOST:/tmp/cock-monitor-staging/"
rsync -avz "$REPO_ROOT/telegram_bot/" "$DEPLOY_HOST:/tmp/cock-monitor-staging/telegram_bot/"
rsync -avz "$REPO_ROOT/systemd/" "$DEPLOY_HOST:/tmp/cock-monitor-staging/"
rsync -avz "$REPO_ROOT/config.example.env" "$REPO_ROOT/README.md" "$DEPLOY_HOST:/tmp/cock-monitor-staging/"

ssh "$DEPLOY_HOST" 'set -e
mkdir -p /opt/cock-monitor/bin /opt/cock-monitor/lib /opt/cock-monitor/telegram_bot /opt/cock-monitor/systemd
install -m755 /tmp/cock-monitor-staging/check-conntrack.sh /opt/cock-monitor/bin/check-conntrack.sh
install -m755 /tmp/cock-monitor-staging/cock-status.sh /opt/cock-monitor/bin/cock-status.sh
install -m644 /tmp/cock-monitor-staging/conntrack-metrics.sh /opt/cock-monitor/lib/conntrack-metrics.sh
cp -a /tmp/cock-monitor-staging/telegram_bot/. /opt/cock-monitor/telegram_bot/
install -m644 /tmp/cock-monitor-staging/cock-monitor.service /opt/cock-monitor/systemd/cock-monitor.service
install -m644 /tmp/cock-monitor-staging/cock-monitor.timer /opt/cock-monitor/systemd/cock-monitor.timer
install -m644 /tmp/cock-monitor-staging/cock-monitor-telegram-bot.service /opt/cock-monitor/systemd/cock-monitor-telegram-bot.service
install -m644 /tmp/cock-monitor-staging/cock-monitor-telegram-bot.timer /opt/cock-monitor/systemd/cock-monitor-telegram-bot.timer
install -m644 /tmp/cock-monitor-staging/config.example.env /opt/cock-monitor/config.example.env
install -m644 /tmp/cock-monitor-staging/README.md /opt/cock-monitor/README.md
chown -R root:root /opt/cock-monitor
rm -rf /tmp/cock-monitor-staging
mkdir -p /var/lib/cock-monitor && chmod 700 /var/lib/cock-monitor
if [ ! -f /etc/cock-monitor.env ]; then
  cp /opt/cock-monitor/config.example.env /etc/cock-monitor.env
  chmod 600 /etc/cock-monitor.env
  echo "Создан /etc/cock-monitor.env — заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID"
fi
install -m644 /opt/cock-monitor/systemd/cock-monitor.service \
  /opt/cock-monitor/systemd/cock-monitor.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cock-monitor.timer
'
```

После первого деплоя отредактируйте на сервере `/etc/cock-monitor.env` и при необходимости проверьте разовый запуск:

```bash
ssh "$DEPLOY_HOST" 'systemctl start cock-monitor.service && systemctl status cock-monitor.service --no-pager'
```

Чтобы включить опрос команд Telegram (`/status`), установите второй timer (нужен **python3** на сервере):

```bash
ssh "$DEPLOY_HOST" 'install -m644 /opt/cock-monitor/systemd/cock-monitor-telegram-bot.service \
  /opt/cock-monitor/systemd/cock-monitor-telegram-bot.timer /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now cock-monitor-telegram-bot.timer'
```

## Обновление (повторный выкат)

Синхронизируйте актуальные файлы приложения в `/opt/cock-monitor` и при изменении unit-файлов обновите systemd.

### Только скрипты, lib, бот и документация (без смены `.service` / `.timer`)

```bash
mkdir -p /tmp/cock-monitor-staging-local/telegram_bot
cp -a "$REPO_ROOT/bin/check-conntrack.sh" "$REPO_ROOT/bin/cock-status.sh" /tmp/cock-monitor-staging-local/
cp -a "$REPO_ROOT/lib/conntrack-metrics.sh" /tmp/cock-monitor-staging-local/
cp -a "$REPO_ROOT/telegram_bot/." /tmp/cock-monitor-staging-local/telegram_bot/
cp -a "$REPO_ROOT/config.example.env" "$REPO_ROOT/README.md" /tmp/cock-monitor-staging-local/
rsync -avz /tmp/cock-monitor-staging-local/ "$DEPLOY_HOST:/tmp/cock-monitor-staging/"
rm -rf /tmp/cock-monitor-staging-local

ssh "$DEPLOY_HOST" 'set -e
install -m755 /tmp/cock-monitor-staging/check-conntrack.sh /opt/cock-monitor/bin/check-conntrack.sh
install -m755 /tmp/cock-monitor-staging/cock-status.sh /opt/cock-monitor/bin/cock-status.sh
install -m644 /tmp/cock-monitor-staging/conntrack-metrics.sh /opt/cock-monitor/lib/conntrack-metrics.sh
cp -a /tmp/cock-monitor-staging/telegram_bot/. /opt/cock-monitor/telegram_bot/
install -m644 /tmp/cock-monitor-staging/config.example.env /opt/cock-monitor/config.example.env
install -m644 /tmp/cock-monitor-staging/README.md /opt/cock-monitor/README.md
chown -R root:root /opt/cock-monitor
rm -rf /tmp/cock-monitor-staging
'
```

`config.example.env` на сервере — справочный шаблон; **боевой конфиг** `/etc/cock-monitor.env` этими командами не трогается. Если появились новые переменные в примере, вручную допишите их в `/etc/cock-monitor.env`.

### Изменились unit-файлы

```bash
mkdir -p /tmp/cock-monitor-staging-local/telegram_bot
cp -a "$REPO_ROOT/bin/check-conntrack.sh" "$REPO_ROOT/bin/cock-status.sh" /tmp/cock-monitor-staging-local/
cp -a "$REPO_ROOT/lib/conntrack-metrics.sh" /tmp/cock-monitor-staging-local/
cp -a "$REPO_ROOT/telegram_bot/." /tmp/cock-monitor-staging-local/telegram_bot/
cp -a "$REPO_ROOT/systemd/cock-monitor.service" "$REPO_ROOT/systemd/cock-monitor.timer" \
  "$REPO_ROOT/systemd/cock-monitor-telegram-bot.service" "$REPO_ROOT/systemd/cock-monitor-telegram-bot.timer" \
  /tmp/cock-monitor-staging-local/
cp -a "$REPO_ROOT/config.example.env" "$REPO_ROOT/README.md" /tmp/cock-monitor-staging-local/
rsync -avz /tmp/cock-monitor-staging-local/ "$DEPLOY_HOST:/tmp/cock-monitor-staging/"
rm -rf /tmp/cock-monitor-staging-local

ssh "$DEPLOY_HOST" 'set -e
install -m755 /tmp/cock-monitor-staging/check-conntrack.sh /opt/cock-monitor/bin/check-conntrack.sh
install -m755 /tmp/cock-monitor-staging/cock-status.sh /opt/cock-monitor/bin/cock-status.sh
install -m644 /tmp/cock-monitor-staging/conntrack-metrics.sh /opt/cock-monitor/lib/conntrack-metrics.sh
cp -a /tmp/cock-monitor-staging/telegram_bot/. /opt/cock-monitor/telegram_bot/
install -m644 /tmp/cock-monitor-staging/cock-monitor.service /opt/cock-monitor/systemd/cock-monitor.service
install -m644 /tmp/cock-monitor-staging/cock-monitor.timer /opt/cock-monitor/systemd/cock-monitor.timer
install -m644 /tmp/cock-monitor-staging/cock-monitor-telegram-bot.service /opt/cock-monitor/systemd/cock-monitor-telegram-bot.service
install -m644 /tmp/cock-monitor-staging/cock-monitor-telegram-bot.timer /opt/cock-monitor/systemd/cock-monitor-telegram-bot.timer
install -m644 /tmp/cock-monitor-staging/config.example.env /opt/cock-monitor/config.example.env
install -m644 /tmp/cock-monitor-staging/README.md /opt/cock-monitor/README.md
chown -R root:root /opt/cock-monitor
rm -rf /tmp/cock-monitor-staging
install -m644 /opt/cock-monitor/systemd/cock-monitor.service \
  /opt/cock-monitor/systemd/cock-monitor.timer \
  /opt/cock-monitor/systemd/cock-monitor-telegram-bot.service \
  /opt/cock-monitor/systemd/cock-monitor-telegram-bot.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart cock-monitor.timer
systemctl try-restart cock-monitor-telegram-bot.timer 2>/dev/null || true
'
```

Проверка таймеров:

```bash
ssh "$DEPLOY_HOST" 'systemctl list-timers cock-monitor.timer cock-monitor-telegram-bot.timer --no-pager'
```

## Важно

- **Не заливайте** локальный файл с секретами в репозиторий; на сервер секреты вносятся только в `/etc/cock-monitor.env`.
- Скрипт рассчитан на **Linux** с доступом к `/proc/sys/net/netfilter/`; на другой ОС или без conntrack поведение см. README.
