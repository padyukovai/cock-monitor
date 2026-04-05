# cock-monitor

Лёгкая проверка заполненности таблицы **nf_conntrack** на Linux VPS с алертами в **Telegram**. Запуск по расписанию (**systemd timer** или **cron**), без постоянного демона, без Prometheus/Grafana и без привязки к MTProxy.

Требования: **bash**, **curl**. Опционально пакет **conntrack** (утилита `conntrack -S` для строки в сообщении и для опциональных алертов по счётчикам).

## Быстрая установка (Ubuntu / Debian)

1. Скопируйте репозиторий на сервер, например в `/opt/cock-monitor`:

   ```bash
   sudo mkdir -p /opt/cock-monitor
   sudo cp -a bin systemd config.example.env README.md /opt/cock-monitor/
   sudo chmod +x /opt/cock-monitor/bin/check-conntrack.sh
   ```

2. Создайте конфиг с секретами:

   ```bash
   sudo cp /opt/cock-monitor/config.example.env /etc/cock-monitor.env
   sudo chmod 600 /etc/cock-monitor.env
   ```

   Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` (см. ниже).

3. Каталог для state-файла (cooldown):

   ```bash
   sudo mkdir -p /var/lib/cock-monitor
   sudo chmod 700 /var/lib/cock-monitor
   ```

   Путь задаётся в `STATE_FILE` в `/etc/cock-monitor.env` (по умолчанию `/var/lib/cock-monitor/state`).

### Настройка бота и chat_id

1. В Telegram откройте [@BotFather](https://t.me/BotFather), создайте бота, скопируйте **токен** в `TELEGRAM_BOT_TOKEN`.
2. Напишите боту любое сообщение (чтобы он мог вам писать).
3. Узнайте **chat_id**:
   - через [@userinfobot](https://t.me/userinfobot), или
   - запросом `https://api.telegram.org/bot<TOKEN>/getUpdates` в браузере/curl после сообщения боту.

Личный чат: положительный `chat_id`. Для группы может понадобиться добавить бота в группу и взять отрицательный `chat_id` из `getUpdates`.

### Проверка вручную

С реальными секретами (отправит сообщение, если сработали пороги и cooldown):

```bash
sudo /opt/cock-monitor/bin/check-conntrack.sh /etc/cock-monitor.env
```

Без Telegram (только вывод текста на экран):

```bash
sudo /opt/cock-monitor/bin/check-conntrack.sh --dry-run /etc/cock-monitor.env
```

Или в `.env`: `DRY_RUN=1` (тогда токен и chat_id не обязательны). Флаг `--dry-run` удобен для разового прогона поверх боевого `.env`.

### Какие файлы в `/proc` читаются

| Путь | Назначение |
|------|------------|
| `/proc/sys/net/netfilter/nf_conntrack_count` | Текущее число записей в conntrack. |
| `/proc/sys/net/netfilter/nf_conntrack_max` | Максимум записей (лимит ядра). |

Доля заполнения: `count / max`; предупреждение и критический уровень задаются `WARN_PERCENT` и `CRIT_PERCENT` в конфиге.

Если файлов нет (модуль не загружен), скрипт завершится с ошибкой и сообщением в stderr — это ожидаемо на хостах без conntrack.

## systemd (рекомендуется)

В unit-файле [`systemd/cock-monitor.service`](systemd/cock-monitor.service) путь `ExecStart` по умолчанию указывает на `/opt/cock-monitor/...`. При другом расположении отредактируйте файл или создайте drop-in:

```bash
sudo install -m644 systemd/cock-monitor.service systemd/cock-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cock-monitor.timer
systemctl list-timers cock-monitor.timer
```

Проверка разового запуска:

```bash
sudo systemctl start cock-monitor.service
sudo systemctl status cock-monitor.service
```

Интервал в [`systemd/cock-monitor.timer`](systemd/cock-monitor.timer): по умолчанию **5 минут** после предыдущего завершения (`OnUnitActiveSec=5min`). Для более редких проверок увеличьте значение.

## cron

Пример см. [`examples/crontab`](examples/crontab). Подставьте реальные пути к скрипту и `.env`.

Не перенаправляйте вывод в большие файлы без ротации: на маленьком диске это быстро забивает корень.

## Конфигурация

Шаблон без секретов: [`config.example.env`](config.example.env).

Основные переменные:

- `WARN_PERCENT`, `CRIT_PERCENT` — пороги по проценту заполнения.
- `COOLDOWN_SECONDS` — минимальный интервал между **повторными** алертами одного уровня заполнения; переход **warning → critical** отправляется сразу (эскалация).
- `CHECK_CONNTRACK_FILL=0` — отключить проверку заполнения (остаются только опциональные stats-алерты).
- `INCLUDE_CONNTRACK_STATS_LINE=0` — не добавлять строку `conntrack -S` в текст fill-алерта.

### Опционально: алерты по `conntrack -S`

Счётчики **drop** и **insert_failed** в выводе `conntrack -S` суммируются по всем строкам (несколько CPU). Это **кумулятивные** значения с момента загрузки/сброса; пороги — грубый индикатор «уже накопилось много».

Включение в `.env`:

```env
ALERT_ON_STATS=1
STATS_DROP_MIN=1000
STATS_INSERT_FAILED_MIN=50
STATS_COOLDOWN_SECONDS=3600
```

Ноль в `STATS_DROP_MIN` / `STATS_INSERT_FAILED_MIN` означает «не использовать этот порог». Для чтения статистики обычно нужен запуск от **root** (или capabilities).

## Логи и диск

Скрипт сам по себе почти ничего не пишет на диск, кроме небольшого **state**-файла для cooldown. Не включайте избыточное логирование cron в файлы без `logrotate`.

## Критерий успеха

После настройки токена, `chat_id` и timer/cron при высоком проценте заполнения `nf_conntrack` вы получаете предсказуемые сообщения в Telegram, без постоянного процесса и тяжёлых зависимостей.
