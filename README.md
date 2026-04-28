# cock-monitor

Лёгкая проверка заполненности таблицы **nf_conntrack** на Linux VPS с алертами в **Telegram**. Запуск по расписанию (**systemd timer** или **cron**), без постоянного демона, без Prometheus/Grafana и без привязки к MTProxy.

Требования: **bash**, **curl**, **Python 3** (модуль `cock_monitor` в том же дереве, что и `bin/` — политика алертов conntrack, запись conntrack/host-метрик в SQLite через стандартный модуль **`sqlite3`**). Опционально пакет **conntrack** (утилита `conntrack -S` для строки в сообщении и для опциональных алертов по счётчикам). Для истории метрик и дельта-алертов нужен каталог **`/var/lib/cock-monitor`** (или другой путь к `METRICS_DB`); утилита **`sqlite3`** (CLI) не обязательна для записи, но удобна для **ручных запросов** к `METRICS_DB` (примеры ниже). Для команд бота **`/status`** и **`/chart`** в Telegram нужны **Python 3**; **`/chart`** и суточный отчёт по таймеру требуют **matplotlib** (удобнее всего пакет ОС `python3-matplotlib`, см. [requirements-chart.txt](requirements-chart.txt)). Опциональный **systemd timer** (или **cron**), см. ниже.

## Быстрая установка (Ubuntu / Debian)

### One-shot инсталлятор (рекомендуется для первого запуска)

Из корня клонированного репозитория:

```bash
sudo bash install/install-ubuntu-minimal.sh
```

Что делает скрипт:

- интерактивно спрашивает `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` (с подсказками);
- устанавливает минимальные зависимости через `apt` (включая `conntrack` и `python3-matplotlib`);
- создаёт `.venv` в текущем клоне и ставит проект + `chart` extras;
- создаёт `/etc/cock-monitor.env` (права `600`) и `/var/lib/cock-monitor` (права `700`);
- устанавливает и включает базовые таймеры:
  - `cock-monitor.timer`
  - `cock-monitor-telegram-bot.timer`
  - `cock-monitor-daily.timer`

Скрипт рассчитан на запуск из текущего клона (без копирования в `/opt/cock-monitor`): для systemd создаются override-конфиги с `WorkingDirectory` на текущий путь репозитория и запуском через `.venv/bin/python`.

### Ручная установка (альтернатива)

1. Скопируйте репозиторий на сервер, например в `/opt/cock-monitor`:

   ```bash
   sudo mkdir -p /opt/cock-monitor
   sudo cp -a bin lib telegram_bot cock_monitor systemd config.example.env config.minimal.env README.md /opt/cock-monitor/
   sudo chmod +x /opt/cock-monitor/bin/check-conntrack.sh /opt/cock-monitor/bin/cock-status.sh
   ```

2. Создайте конфиг с секретами:

   ```bash
   sudo cp /opt/cock-monitor/config.minimal.env /etc/cock-monitor.env
   sudo chmod 600 /etc/cock-monitor.env
   ```

   Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` (см. ниже).

3. Каталог для state-файла (cooldown):

   ```bash
   sudo mkdir -p /var/lib/cock-monitor
   sudo chmod 700 /var/lib/cock-monitor
   ```

   Путь задаётся в `STATE_FILE` в `/etc/cock-monitor.env` (по умолчанию `/var/lib/cock-monitor/state`). Для опроса команд Telegram дополнительно пишется файл смещения `getUpdates` (по умолчанию рядом с каталогом state, см. `TELEGRAM_OFFSET_FILE` в [`config.example.env`](config.example.env)).

### Проверка окружения перед деплоем

После копирования дерева в `/opt/cock-monitor` можно проверить наличие утилит на `PATH` и (если есть файл конфигурации) дополнительные зависимости по сценарию:

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor preflight /etc/cock-monitor.env
```

Явная валидация самого `.env` (диапазоны, зависимые ключи, предупреждения):

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor config-check /etc/cock-monitor.env
```

Если `/etc/cock-monitor.env` ещё не создан, команда всё равно проверит `python3`, `curl` и `sqlite3`; путь к env можно не указывать — по умолчанию используется `/etc/cock-monitor.env`, при отсутствии файла выводится предупреждение и пропускаются проверки, зависящие от переменных. Только базовые проверки: `python3 -m cock_monitor preflight --minimal`.

### Настройка бота и chat_id

1. В Telegram откройте [@BotFather](https://t.me/BotFather), создайте бота, скопируйте **токен** в `TELEGRAM_BOT_TOKEN`.
2. Напишите боту любое сообщение (чтобы он мог вам писать).
3. Узнайте **chat_id**:
   - через [@userinfobot](https://t.me/userinfobot), или
   - запросом `https://api.telegram.org/bot<TOKEN>/getUpdates` в браузере/curl после сообщения боту.

Личный чат: положительный `chat_id`. Для группы может понадобиться добавить бота в группу и взять отрицательный `chat_id` из `getUpdates`.

### Проверка вручную

С реальными секретами (канонический entrypoint; отправит сообщение, если сработали пороги и cooldown):

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor conntrack-check /etc/cock-monitor.env
```

Без Telegram (только вывод текста на экран):

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor conntrack-check --dry-run /etc/cock-monitor.env
```

Или в `.env`: `DRY_RUN=1` (тогда токен и chat_id не обязательны). Флаг `--dry-run` удобен для разового прогона поверх боевого `.env`.

Текущий статус conntrack в консоли (без Telegram):

```bash
sudo /opt/cock-monitor/bin/cock-status.sh /etc/cock-monitor.env
```

### Опционально: команды в Telegram (`/status`, `/chart`, `/vless_delta`)

Алерты по расписанию шлёт `python -m cock_monitor conntrack-check` (в `systemd/cock-monitor.service`). Чтобы **по запросу** получать полный текст статуса в том же чате, включите опрос **getUpdates** без постоянного демона: разовый запуск Python по таймеру.

- **`/chart`** строит PNG за последние 24 часа из `METRICS_DB` тем же скриптом, что и ежедневный таймер; на сервере должен быть установлен **matplotlib**.
- **`/vless_delta`** запускает `python -m cock_monitor vless-report --mode since-last-sent --send-telegram` и отправляет отчёт по VLESS-дельте с момента **предыдущего `/vless_delta` или другого запуска в том же режиме**.
- **`/cake_bw <mbit>`** обновляет `SHAPER_MAX_RATE_MBIT` в `.env` (верхний лимит CAKE для `cock-cpu-shaper.sh`); новое значение применяется на ближайшем тике `cock-shaper.timer`.

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

Ручной запуск того же, что делает service (нужен `COCK_MONITOR_HOME`):

```bash
cd /opt/cock-monitor && sudo env COCK_MONITOR_HOME=/opt/cock-monitor \
  python3 -m telegram_bot --poll-once /etc/cock-monitor.env
```

### Опциональный модуль MTProxy

`cock-monitor` может работать как единая экосистема и для conntrack, и для MTProxy, без второго poller-а Telegram.  
Включение делается через `.env`:

```env
MTPROXY_ENABLE=1
MTPROXY_PORT=8443
MTPROXY_MAX_CONNECTIONS_PER_IP=20
MTPROXY_MAX_UNIQUE_IPS=50
MTPROXY_ALERT_COOLDOWN_MINUTES=30
MTPROXY_DAILY_TOP_N=10
```

Команды Telegram (в том же общем `/help`):

- `/mt_status` — текущий статус MTProxy
- `/mt_today` — отчёт + PNG за последние 24 часа
- `/mt_threshold warning 30` — изменить порог per-IP
- `/mt_threshold critical 100` — изменить порог unique IPs

Пороги `/mt_threshold` сохраняются в `METRICS_DB` (таблица `mtproxy_state`) и применяются как для команд, так и для алертов коллектора.

Таблицы модуля в общем `METRICS_DB`:

- `mtproxy_metrics`
- `mtproxy_alerts`
- `mtproxy_state`
- `mtproxy_ip_geo_cache`

Таймеры модуля:

- [`systemd/cock-mtproxy-monitor.timer`](systemd/cock-mtproxy-monitor.timer) + [`systemd/cock-mtproxy-monitor.service`](systemd/cock-mtproxy-monitor.service) — сбор/алерты (каждые 5 минут)
- [`systemd/cock-mtproxy-daily.timer`](systemd/cock-mtproxy-daily.timer) + [`systemd/cock-mtproxy-daily.service`](systemd/cock-mtproxy-daily.service) — суточный отчёт в Telegram

Установка unit-файлов:

```bash
sudo install -m644 /opt/cock-monitor/systemd/cock-mtproxy-monitor.service \
  /opt/cock-monitor/systemd/cock-mtproxy-monitor.timer \
  /opt/cock-monitor/systemd/cock-mtproxy-daily.service \
  /opt/cock-monitor/systemd/cock-mtproxy-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cock-mtproxy-monitor.timer cock-mtproxy-daily.timer
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

## Upgrade для существующих инсталляций

Короткий migration path для breaking-изменений operational-контракта вынесен в [`DEPLOY.md`](DEPLOY.md), раздел **`Upgrade guide (breaking-aware)`**: backup `env`/`metrics.db`, обновление unit-файлов, `preflight`, smoke и rollback.

## Конфигурация

Стартовый шаблон: [`config.minimal.env`](config.minimal.env) (быстрый старт).  
Полный reference по всем подсистемам: [`config.example.env`](config.example.env).

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

При `METRICS_RECORD_EVERY_RUN=1` и/или `ALERT_ON_STATS_DELTA=1` скрипт пишет строки в **`METRICS_DB`** (по умолчанию `/var/lib/cock-monitor/metrics.db`): время, заполнение из `/proc`, суммы полей `conntrack -S` по CPU (`drop`, `insert_failed`, `early_drop`, `error`, `invalid`, `search_restart`) и дельты к предыдущей строке. Запись выполняется через **Python** (`cock_monitor.storage`); отдельный бинарник **`sqlite3`** не требуется.

**Версия схемы:** в той же базе создаётся таблица **`cock_monitor_schema`** (`component`, `version`). Для таблиц `conntrack_samples` и `host_samples` используется компонент **`conntrack_host`**; номер версии обновляется только миграциями этого репозитория и не пересекается с другими подсистемами (VLESS, MTProxy), которые живут в том же файле `METRICS_DB`.

- **Первый замер** после пустой базы только инициализирует строку; алерты по дельте **не** отправляются.
- **Кумулятивные** пороги `STATS_*_MIN` и **дельта/rate** (`ALERT_ON_STATS_DELTA`, `STATS_DELTA_*`, `STATS_RATE_*_PER_MIN`) могут работать **одновременно**; исходящие сообщения объединяются в один блок «STATS», общий cooldown задаётся **`STATS_COOLDOWN_SECONDS`**.
- В конец текста STATS добавляется короткий **контекст хоста** (`load1`, `MemAvailable`, swap, строка `TCP:` из `/proc/net/sockstat`, строка шейпера из `SHAPER_STATUS_FILE` если `ts=` не старше **`STATS_ALERT_SHAPER_MAX_AGE_MIN`** минут, иначе `shaper: no data`). При **`STATS_ALERT_SHAPER_MAX_AGE_MIN=0`** возраст `ts` не проверяется.
- Дельты считаются с учётом возможного **переполнения 32-bit** счётчиков ядра (обёртка modulo 2³²).
- **`METRICS_RETENTION_DAYS`** удаляет старые строки; **`METRICS_MAX_ROWS`** (если > 0) ограничивает число последних записей.
- Режим **`DRY_RUN=1`** (или CLI `--dry-run`) **не** пишет в базу и не трогает retention.

**Таблица `host_samples`** (тот же `METRICS_DB`): при каждой записи в `conntrack_samples` добавляется строка с тем же **`ts`** — нагрузка и память для постмортема.

| Колонка | Смысл |
|--------|--------|
| `ts` | Unix time (как в `conntrack_samples`) |
| `load1` | 1‑минутный load average из `/proc/loadavg` |
| `mem_avail_kb` | `MemAvailable` из `/proc/meminfo` (kB) |
| `swap_used_kb` | `SwapTotal − SwapFree` (kB), если оба поля есть |
| `tcp_inuse`, `tcp_orphan`, `tcp_tw` | из строки `TCP:` в `/proc/net/sockstat` |
| `tcp6_inuse` | из строки `TCP6:` |
| `shaper_rate_mbit`, `shaper_cpu_pct` | из файла статуса шейпера (`SHAPER_STATUS_FILE`), если есть |
| `tc_qdisc_root` | первая строка `tc qdisc show dev <SHAPER_IFACE|ens3> root` (до 400 символов); отключить: **`METRICS_COLLECT_TC_QDISC=0`** |

Удаление по возрасту затрагивает и **`host_samples`**, и **`conntrack_samples`**. После обрезки по **`METRICS_MAX_ROWS`** «лишние» строки **`host_samples`** без пары в `conntrack_samples` удаляются автоматически.

Пример: что было на хосте за последний час (границы в epoch можно задать из времени жалобы):

```bash
T0=$(( $(date +%s) - 3600 ))
T1=$(date +%s)
sqlite3 /var/lib/cock-monitor/metrics.db "
SELECT datetime(c.ts, 'unixepoch'), c.fill_pct, h.load1, h.mem_avail_kb, h.swap_used_kb,
       h.tcp_inuse, h.tcp_orphan, h.tcp_tw, h.shaper_rate_mbit
FROM conntrack_samples c
LEFT JOIN host_samples h ON h.ts = c.ts
WHERE c.ts >= ${T0} AND c.ts <= ${T1}
ORDER BY c.ts;
"
```

Рекомендуемый поток: начать с [`config.minimal.env`](config.minimal.env), затем по мере включения модулей переносить нужные блоки из [`config.example.env`](config.example.env).  
После каждого изменения запускать `python3 -m cock_monitor config-check /etc/cock-monitor.env`.

### Суточный график в Telegram

Команда `python -m cock_monitor daily-chart` читает `METRICS_DB`, строит PNG (доля заполнения и дельты по интервалам) и может отправить его через Bot API.

- По расписанию: unit-файлы [`systemd/cock-monitor-daily.service`](systemd/cock-monitor-daily.service) и [`systemd/cock-monitor-daily.timer`](systemd/cock-monitor-daily.timer) (по умолчанию **00:05**). Нужны **matplotlib** и те же `TELEGRAM_*`, что и для алертов.
- Окно в часах задаётся **`DAILY_CHART_HOURS`** в `.env` (по умолчанию 24) или флагом `--hours` при ручном запуске.

Пример ручной генерации файла без отправки:

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor daily-chart --env-file /etc/cock-monitor.env --output /tmp/cock.png
```

### Суточный отчёт по VLESS-клиентам 3x-ui

Команда `python -m cock_monitor vless-report` читает счётчики `up/down` из `3x-ui` SQLite (`XUI_DB_PATH`) в read-only режиме, сохраняет snapshot в `METRICS_DB`, считает дельты по `email` и отправляет Top потребителей в Telegram.

- Таймзона отчёта задаётся `VLESS_DAILY_TZ` (по умолчанию `Europe/Moscow`).
- Время в тексте Telegram (например момент последней отправки в `since-last-sent`) показывается в `VLESS_TELEGRAM_DISPLAY_TZ` (по умолчанию `Europe/Moscow`), независимо от TZ контейнера 3x-ui.
- По расписанию: unit-файлы [`systemd/cock-vless-daily.service`](systemd/cock-vless-daily.service) и [`systemd/cock-vless-daily.timer`](systemd/cock-vless-daily.timer), время по умолчанию **00:10 MSK** (для старого `systemd` таймер задаётся как `21:10 UTC`).
- Таймерный запуск использует `--mode daily` (строго `D` против `D-1` в `VLESS_DAILY_TZ`).
- Режим `since-last-sent` используется для ручного запроса `/vless_delta` и не влияет на daily-таймер.
- Пороги «злостного качальщика»: `VLESS_ABUSE_GB` (абсолютный) и `VLESS_ABUSE_SHARE_PCT` (доля от суточного total, с защитным минимумом `VLESS_DAILY_MIN_TOTAL_MB`).
- На первом запуске делается baseline, полноценный daily-отчёт начинается со следующего запуска.

Опционально: **уникальные IP по `email`** (детект шаринга конфига). Нужен Xray `access.log` на хосте (например bind-mount файла в `/app/access.log` в контейнере 3x-ui) и переменные:

- `VLESS_ACCESS_LOG_PATH` — путь к access.log на хосте.
- **Всё по московскому времени:** держите `VLESS_DAILY_TZ=Europe/Moscow` и запускайте контейнер 3x-ui/Xray с `TZ=Europe/Moscow`, чтобы строки в `access.log` были в MSK; тогда `VLESS_ACCESS_LOG_TZ` можно не задавать (по умолчанию совпадёт с `VLESS_DAILY_TZ`). Если контейнер остаётся в другой TZ, задайте `VLESS_ACCESS_LOG_TZ` в ту TZ, **в которой реально пишутся** метки времени в логе (иначе в stderr будет `ip_lines_matched=0` при живом логе — сдвиг интерпретации времени).
- `VLESS_IP_TOP_K` — размер отдельного топа по числу уникальных IP (по умолчанию 3).
- `VLESS_IP_PARSE_MAX_MB` — лимит чтения лога за один запуск (`daily`: с начала файла; `since-last-sent`: хвост; защита для слабых VPS).

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
cd /opt/cock-monitor && sudo python3 -m cock_monitor vless-report --env-file /etc/cock-monitor.env --dry-run
```

Явно запросить режим `daily` (сравнение `D` против `D-1`):

```bash
cd /opt/cock-monitor && sudo python3 -m cock_monitor vless-report --env-file /etc/cock-monitor.env --dry-run --mode daily
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

Скрипт [`bin/cock-cpu-shaper.sh`](bin/cock-cpu-shaper.sh) (запускается по расписанию `cock-shaper.timer` каждые 10-15 секунд) остаётся на bash: напрямую вызывает `tc`/`ip`, читает `/proc/stat` и атомарно обновляет state-файлы; перенос в Python не упрощает сценарий. Он отслеживает загрузку CPU: если `cpu_pct` превышает пороговое значение, скрипт «прикручивает вентиль» — плавно снижает пропускную способность для VPN портов. Если нагрузка падает — скрипт вновь поднимает лимит.

Главная фишка: для ограничения скорости используется встроенный в ядро планировщик **sch_cake** в режиме `dual-dsthost`. Он автоматически делит установленную ширину канала строго поровну между всеми качающими клиентами! Никакого "встал торрент - лег VPN".

- **Конфиг:** блок `SHAPER_*` в [`config.example.env`](config.example.env). Включение: **`SHAPER_ENABLE=1`**. Важно указать порты Xray/VPN: `SHAPER_VPN_PORTS=443,2053,37346`.
- **Расписание:** [`systemd/cock-shaper.timer`](systemd/cock-shaper.timer) (по умолчанию запускается каждые 10 секунд).
- **Режим выключения:** при `SHAPER_ENABLE=0` скрипт не меняет `tc` (без teardown). Рекомендуется выключать таймер отдельно: `sudo systemctl disable --now cock-shaper.timer`; если таймер останется включён, скрипт запишет warning в stderr/`SHAPER_STATUS_FILE`.
- **Проверка:** для проверки в холостую: `sudo /opt/cock-monitor/bin/cock-cpu-shaper.sh --dry-run /etc/cock-monitor.env`

### Incident sampler (короткие постмортем-срезы)

Сервис incident sampler запускает Python-модуль `cock_monitor.services.incident_sampler` (unit: [`systemd/cock-monitor-incident-sampler.service`](systemd/cock-monitor-incident-sampler.service)) и пишет в JSONL короткие срезы состояния сети. Это помогает разбирать минутные деградации VPN/панели по фактам, а не по косвенным признакам.

- **Зависимости:** на хосте должна быть команда **`ping`** (Debian/Ubuntu: пакет **`iputils-ping`**), иначе loss/latency в JSON будут некорректны.
- **Метрики в срезе:** ping-loss/latency, DNS probe, `nf_conntrack count/max`, TCP state counts, **TCP-probe по прикладным портам** (опционально), `load1`, `MemAvailable`, `systemctl is-active` для выбранных unit.
- **Конфиг:** блок `INCIDENT_*` в [`config.example.env`](config.example.env). Для включения задайте `INCIDENT_SAMPLER_ENABLE=1`.
- **Группы ping-таргетов для диагностики:** в JSON и post-mortem добавлены `ping_groups`:
  - `gateway` — автоопределение next-hop из default route;
  - `internal` — `INCIDENT_PING_INTERNAL_TARGETS`;
  - `external` — `INCIDENT_PING_EXTERNAL_TARGETS`.
  Это диагностическая телеметрия: текущая логика WARN/CRIT по `INCIDENT_PING_LOSS_WARN_PCT` не меняется.
- **TCP-probe (рекомендуется):** задайте `INCIDENT_TCP_PROBE_PORTS="443 2053 37346"` и оба target: `INCIDENT_TCP_PROBE_LOCAL_TARGET=127.0.0.1` + `INCIDENT_TCP_PROBE_EXTERNAL_TARGET=<PUBLIC_IP_OR_DNS>`. Тогда в каждом срезе считаются отдельные fail-счётчики local/external и общий итог. Пороговые параметры: `INCIDENT_TCP_PROBE_WARN_FAILS`, `INCIDENT_TCP_PROBE_CRIT_FAILS`.
- **Расписание:** [`systemd/cock-monitor-incident-sampler.timer`](systemd/cock-monitor-incident-sampler.timer) (по умолчанию каждые 10 секунд).
- **Логи:** `${INCIDENT_LOG_DIR}/incident-YYYYMMDD.jsonl` (по умолчанию `/var/lib/cock-monitor`).
- **Проверка вручную:** `cd /opt/cock-monitor && sudo INCIDENT_SAMPLER_ENABLE=1 python3 -m cock_monitor.services.incident_sampler /etc/cock-monitor.env`
- **Post-mortem в Telegram:** при переходе **WARN/CRIT → OK** скрипт [`bin/incident-postmortem.py`](bin/incident-postmortem.py) читает JSONL за окно инцидента и отправляет краткий HTML-отчёт (нужны **python3** и `INCIDENT_POSTMORTEM_ENABLE=1`).

## Логи и диск

Скрипт проверки пишет небольшой **state**-файл для cooldown и при включённых метриках — файл **`metrics.db`** (порядок десятков килобайт на типичном интервале; см. retention). При включённом опросе бота добавляется файл **offset** для `getUpdates` (`TELEGRAM_OFFSET_FILE`). Не включайте избыточное логирование cron в файлы без `logrotate`.

## Разработка и тесты

Политика алертов conntrack (cooldown, STATS и т.д.) и слой SQLite для `conntrack_samples` / `host_samples` вынесены в пакет `cock_monitor` и покрыты unit-тестами. Зависимости для разработки задаются в [`pyproject.toml`](pyproject.toml) (extra `dev`: pytest, ruff). Установка и тесты из корня репозитория:

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Эквивалентно: `pip install -r requirements-dev.txt` (editable-пакет и dev-инструменты). Для локальной отладки графиков (`/chart`, MTProxy PNG) дополнительно: `pip install -e ".[chart]"` или [`requirements-chart.txt`](requirements-chart.txt). В CI на push/PR выполняются `ruff` и `pytest` (см. [`.github/workflows/python-ci.yml`](.github/workflows/python-ci.yml)).

## Критерий успеха

После настройки токена, `chat_id` и timer/cron при высоком проценте заполнения `nf_conntrack` вы получаете предсказуемые сообщения в Telegram, без постоянного процесса и тяжёлых зависимостей.
