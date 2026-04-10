# cock-monitor

Лёгкая проверка заполненности таблицы **nf_conntrack** на Linux VPS с алертами в **Telegram**. Запуск по расписанию (**systemd timer** или **cron**), без постоянного демона, без Prometheus/Grafana и без привязки к MTProxy.

Требования: **bash**, **curl**. Опционально пакет **conntrack** (утилита `conntrack -S` для строки в сообщении и для опциональных алертов по счётчикам). Для истории метрик в SQLite и дельта-алертов нужны **sqlite3** (CLI) и каталог **`/var/lib/cock-monitor`**. Для команд бота **`/status`** и **`/chart`** в Telegram нужны **Python 3**; **`/chart`** и суточный отчёт по таймеру требуют **matplotlib** (удобнее всего пакет ОС `python3-matplotlib`, см. [requirements-chart.txt](requirements-chart.txt)). Опциональный **systemd timer** (или **cron**), см. ниже.

## Быстрая установка (Ubuntu / Debian)

1. Скопируйте репозиторий на сервер, например в `/opt/cock-monitor`:

   ```bash
   sudo mkdir -p /opt/cock-monitor
   sudo cp -a bin lib telegram_bot systemd config.example.env README.md /opt/cock-monitor/
   sudo chmod +x /opt/cock-monitor/bin/check-conntrack.sh /opt/cock-monitor/bin/cock-status.sh /opt/cock-monitor/bin/cock-daily-chart.py /opt/cock-monitor/bin/cock-vless-daily-report.py
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

   Путь задаётся в `STATE_FILE` в `/etc/cock-monitor.env` (по умолчанию `/var/lib/cock-monitor/state`). Для опроса команд Telegram дополнительно пишется файл смещения `getUpdates` (по умолчанию рядом с каталогом state, см. `TELEGRAM_OFFSET_FILE` в [`config.example.env`](config.example.env)).

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

Текущий статус conntrack в консоли (без Telegram):

```bash
sudo /opt/cock-monitor/bin/cock-status.sh /etc/cock-monitor.env
```

### Опционально: команды в Telegram (`/status`, `/chart`, `/vless_delta`)

Алерты по-прежнему шлёт `check-conntrack.sh` по расписанию. Чтобы **по запросу** получать полный текст статуса в том же чате, включите опрос **getUpdates** без постоянного демона: разовый запуск Python по таймеру.

- **`/chart`** строит PNG за последние 24 часа из `METRICS_DB` тем же скриптом, что и ежедневный таймер; на сервере должен быть установлен **matplotlib**.
- **`/vless_delta`** запускает `bin/cock-vless-daily-report.py` в режиме `since-last-sent` и отправляет отчёт по VLESS-дельте с момента **предыдущего `/vless_delta` или другого запуска в том же режиме**.

- Команды обрабатываются только из чата с вашим `TELEGRAM_CHAT_ID` (как и исходящие алерты).
- Максимальная задержка ответа ≈ периоду таймера (по умолчанию **3 минуты** в [`systemd/cock-monitor-telegram-bot.timer`](systemd/cock-monitor-telegram-bot.timer)).
- Не включайте **webhook** для того же бота и не запускайте два параллельных опроса с одним токеном.

Установка unit-файлов (пути подправьте при необходимости):

```bash
sudo install -m644 /opt/cock-monitor/systemd/cock-monitor-telegram-bot.service \
  /opt/cock-monitor/systemd/cock-monitor-telegram-bot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cock-monitor-telegram-bot.timer
systemctl list-timers cock-monitor-telegram-bot.timer --no-pager
```

Разовая проверка:

```bash
sudo systemctl start cock-monitor-telegram-bot.service
sudo systemctl status cock-monitor-telegram-bot.service --no-pager
```

Ручной запуск того же, что делает service (нужны `PYTHONPATH` и `COCK_MONITOR_HOME`):

```bash
sudo env PYTHONPATH=/opt/cock-monitor COCK_MONITOR_HOME=/opt/cock-monitor \
  python3 -m telegram_bot --poll-once /etc/cock-monitor.env
```

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

### История в SQLite и алерты по дельте / скорости

При `METRICS_RECORD_EVERY_RUN=1` и/или `ALERT_ON_STATS_DELTA=1` скрипт пишет строки в **`METRICS_DB`** (по умолчанию `/var/lib/cock-monitor/metrics.db`): время, заполнение из `/proc`, суммы полей `conntrack -S` по CPU (`drop`, `insert_failed`, `early_drop`, `error`, `invalid`, `search_restart`) и дельты к предыдущей строке. Нужен исполняемый **`sqlite3`**.

- **Первый замер** после пустой базы только инициализирует строку; алерты по дельте **не** отправляются.
- **Кумулятивные** пороги `STATS_*_MIN` и **дельта/rate** (`ALERT_ON_STATS_DELTA`, `STATS_DELTA_*`, `STATS_RATE_*_PER_MIN`) могут работать **одновременно**; исходящие сообщения объединяются в один блок «STATS», общий cooldown задаётся **`STATS_COOLDOWN_SECONDS`**.
- Дельты считаются с учётом возможного **переполнения 32-bit** счётчиков ядра (обёртка modulo 2³²).
- **`METRICS_RETENTION_DAYS`** удаляет старые строки; **`METRICS_MAX_ROWS`** (если > 0) ограничивает число последних записей.
- Режим **`DRY_RUN=1`** (или CLI `--dry-run`) **не** пишет в базу и не трогает retention.

Подробные переменные см. в [`config.example.env`](config.example.env).

### Суточный график в Telegram

Скрипт [`bin/cock-daily-chart.py`](bin/cock-daily-chart.py) читает `METRICS_DB`, строит PNG (доля заполнения и дельты по интервалам) и может отправить его через Bot API.

- По расписанию: unit-файлы [`systemd/cock-monitor-daily.service`](systemd/cock-monitor-daily.service) и [`systemd/cock-monitor-daily.timer`](systemd/cock-monitor-daily.timer) (по умолчанию **00:05**). Нужны **matplotlib** и те же `TELEGRAM_*`, что и для алертов.
- Окно в часах задаётся **`DAILY_CHART_HOURS`** в `.env` (по умолчанию 24) или флагом `--hours` при ручном запуске.

Пример ручной генерации файла без отправки:

```bash
sudo python3 /opt/cock-monitor/bin/cock-daily-chart.py --env-file /etc/cock-monitor.env --output /tmp/cock.png
```

### Суточный отчёт по VLESS-клиентам 3x-ui

Скрипт [`bin/cock-vless-daily-report.py`](bin/cock-vless-daily-report.py) читает счётчики `up/down` из `3x-ui` SQLite (`XUI_DB_PATH`) в read-only режиме, сохраняет snapshot в `METRICS_DB`, считает дельты по `email` и отправляет Top потребителей в Telegram.

- Таймзона отчёта задаётся `VLESS_DAILY_TZ` (по умолчанию `Europe/Moscow`).
- По расписанию: unit-файлы [`systemd/cock-vless-daily.service`](systemd/cock-vless-daily.service) и [`systemd/cock-vless-daily.timer`](systemd/cock-vless-daily.timer), время по умолчанию **00:10 MSK** (для старого `systemd` таймер задаётся как `21:10 UTC`).
- Таймерный запуск использует `--mode daily` (строго `D` против `D-1` в `VLESS_DAILY_TZ`).
- Режим `since-last-sent` используется для ручного запроса `/vless_delta` и не влияет на daily-таймер.
- Пороги «злостного качальщика»: `VLESS_ABUSE_GB` (абсолютный) и `VLESS_ABUSE_SHARE_PCT` (доля от суточного total, с защитным минимумом `VLESS_DAILY_MIN_TOTAL_MB`).
- На первом запуске делается baseline, полноценный daily-отчёт начинается со следующего запуска.

Установка таймера:

```bash
sudo install -m644 /opt/cock-monitor/systemd/cock-vless-daily.service \
  /opt/cock-monitor/systemd/cock-vless-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cock-vless-daily.timer
systemctl list-timers cock-vless-daily.timer --no-pager
```

Ручная проверка без отправки в Telegram:

```bash
sudo python3 /opt/cock-monitor/bin/cock-vless-daily-report.py --env-file /etc/cock-monitor.env --dry-run
```

Явно запросить режим `daily` (сравнение `D` против `D-1`):

```bash
sudo python3 /opt/cock-monitor/bin/cock-vless-daily-report.py --env-file /etc/cock-monitor.env --dry-run --mode daily
```

Пример содержимого отчёта:

```text
host123 — VLESS daily usage (MSK): 2026-04-10
Total: 48.21 GB | Active clients: 17 | Top1 share: 36.9%

Top 10:
1) user1@example.com — 17.80 GB (36.9%)
2) user2@example.com — 8.45 GB (17.5%)
...

Potential heavy downloaders:
- user1@example.com: 17.80 GB (36.9%)
```

### Умный "CPU-Aware" Шейпер с использованием CAKE

Скрипт [`bin/cock-cpu-shaper.sh`](bin/cock-cpu-shaper.sh) (запускается по расписанию `cock-shaper.timer` каждые 10-15 секунд) отслеживает загрузку CPU. Если `cpu_pct` (вычисляется из /proc/stat) превышает пороговое значение, скрипт "прикручивает вентиль" - плавно снижает пропускную способность для VPN портов. Если нагрузка падает - скрипт вновь поднимает лимит.

Главная фишка: для ограничения скорости используется встроенный в ядро планировщик **sch_cake** в режиме `dual-dsthost`. Он автоматически делит установленную ширину канала строго поровну между всеми качающими клиентами! Никакого "встал торрент - лег VPN".

- **Конфиг:** блок `SHAPER_*` в [`config.example.env`](config.example.env). Включение: **`SHAPER_ENABLE=1`**. Важно указать порты Xray/VPN: `SHAPER_VPN_PORTS=443,2053,37346`.
- **Расписание:** [`systemd/cock-shaper.timer`](systemd/cock-shaper.timer) (по умолчанию запускается каждые 10 секунд).
- **Проверка:** для проверки в холостую: `sudo /opt/cock-monitor/bin/cock-cpu-shaper.sh --dry-run /etc/cock-monitor.env`

## Логи и диск

Скрипт проверки пишет небольшой **state**-файл для cooldown и при включённых метриках — файл **`metrics.db`** (порядок десятков килобайт на типичном интервале; см. retention). При включённом опросе бота добавляется файл **offset** для `getUpdates` (`TELEGRAM_OFFSET_FILE`). Не включайте избыточное логирование cron в файлы без `logrotate`.

## Критерий успеха

После настройки токена, `chat_id` и timer/cron при высоком проценте заполнения `nf_conntrack` вы получаете предсказуемые сообщения в Telegram, без постоянного процесса и тяжёлых зависимостей.
