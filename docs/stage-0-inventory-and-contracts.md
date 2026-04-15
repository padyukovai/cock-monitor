# Этап 0 — инвентаризация и контракты

Базовая фиксация текущего поведения до крупных изменений. Источники: `bin/*`, `cock_monitor/*`, `mtproxy_module/*`, `telegram_bot/*`, `systemd/*`, [`README.md`](../README.md), [`config.example.env`](../config.example.env), [`DEPLOY.md`](../DEPLOY.md).

## 1) Матрица сценариев (entrypoint -> входы -> выходы -> side effects -> timer)

| Entrypoint | Входы (CLI/env) | Выходы | Side effects | Timer / запуск |
|---|---|---|---|---|
| [`bin/check-conntrack.sh`](../bin/check-conntrack.sh) | `--dry-run`; env: `WARN_PERCENT`, `CRIT_PERCENT`, `COOLDOWN_SECONDS`, `STATE_FILE`, `CHECK_CONNTRACK_FILL`, `ALERT_ON_STATS*`, `METRICS_*`, `LA_ALERT_*`, `TELEGRAM_*` | Exit code, stderr/stdout (в dry-run) | Чтение `/proc/sys/net/netfilter/*`, `/proc/loadavg`, `/proc/net/dev`; Telegram `sendMessage`; запись `STATE_FILE`; миграция/запись/retention в `METRICS_DB` (`conntrack_samples`, `host_samples`) | [`systemd/cock-monitor.timer`](../systemd/cock-monitor.timer) -> [`systemd/cock-monitor.service`](../systemd/cock-monitor.service), либо ручной запуск |
| [`bin/cock-status.sh`](../bin/cock-status.sh) | env-файл (`ENV_FILE` или argv) | Текст статуса в stdout | Чтение `/proc`, status-файлов шейпера, опционально `conntrack -S` | Ручной запуск (ops/diagnostic) |
| `python -m cock_monitor daily-chart` | `--env-file`, `--hours`, `--output`, `--send-telegram`; env: `METRICS_DB`, `DAILY_CHART_HOURS`, `TELEGRAM_*` | PNG файл, caption, опционально Telegram фото | Чтение `METRICS_DB` (`conntrack_samples`, `host_samples`) | [`systemd/cock-monitor-daily.timer`](../systemd/cock-monitor-daily.timer) -> [`systemd/cock-monitor-daily.service`](../systemd/cock-monitor-daily.service), плюс `/chart` в боте |
| `python -m cock_monitor vless-report` | `--env-file`, `--mode`, `--dry-run`, `--send-telegram`; env: `XUI_DB_PATH`, `METRICS_DB`, `VLESS_*`, `TELEGRAM_*` | HTML-текст отчёта (stdout/Telegram) | Чтение внешней 3x-ui DB (`XUI_DB_PATH`, ro); чтение VLESS access log (если включено); запись таблиц `vless_*` в `METRICS_DB`; отправка Telegram | [`systemd/cock-vless-daily.timer`](../systemd/cock-vless-daily.timer) -> [`systemd/cock-vless-daily.service`](../systemd/cock-vless-daily.service), плюс `/vless_delta` в боте |
| `python -m cock_monitor mtproxy-collect` | `--env-file`; env: `MTPROXY_*`, `METRICS_DB`, `TELEGRAM_*` | Exit code, stderr/stdout | Чтение `ss`, `iptables`; запись `mtproxy_metrics`, `mtproxy_alerts`, `mtproxy_state` в `METRICS_DB`; Telegram алерты | [`systemd/cock-mtproxy-monitor.timer`](../systemd/cock-mtproxy-monitor.timer) -> [`systemd/cock-mtproxy-monitor.service`](../systemd/cock-mtproxy-monitor.service) |
| `python -m cock_monitor mtproxy-daily` | `--env-file`, `--hours`, `--send-telegram`, `--output`; env: `MTPROXY_*`, `METRICS_DB`, `TELEGRAM_*` | PNG + caption, опционально Telegram фото | Чтение `mtproxy_metrics` из `METRICS_DB` | [`systemd/cock-mtproxy-daily.timer`](../systemd/cock-mtproxy-daily.timer) -> [`systemd/cock-mtproxy-daily.service`](../systemd/cock-mtproxy-daily.service), плюс `/mt_today` |
| [`bin/cock-cpu-shaper.sh`](../bin/cock-cpu-shaper.sh) | `--dry-run`; env: `SHAPER_*`, `TELEGRAM_*` | stdout one-line статус, exit code | `tc`/`ip` изменения qdisc/class/filter; запись `SHAPER_STATE_FILE` и `SHAPER_STATUS_FILE`; Telegram при step_up/step_down | [`systemd/cock-shaper.timer`](../systemd/cock-shaper.timer) -> [`systemd/cock-shaper.service`](../systemd/cock-shaper.service) |
| [`bin/incident-sampler.sh`](../bin/incident-sampler.sh) -> `python -m cock_monitor.services.incident_sampler` | env: `INCIDENT_*`, `TELEGRAM_*` | JSONL срезы, опционально Telegram | Чтение ping/DNS/TCP probe/conntrack/systemd; запись `${INCIDENT_LOG_DIR}/incident-YYYYMMDD.jsonl` и `INCIDENT_STATE_FILE`; вызов post-mortem при recovery | [`systemd/cock-monitor-incident-sampler.timer`](../systemd/cock-monitor-incident-sampler.timer) -> [`systemd/cock-monitor-incident-sampler.service`](../systemd/cock-monitor-incident-sampler.service) |
| [`bin/incident-postmortem.py`](../bin/incident-postmortem.py) | `START_EPOCH END_EPOCH LOG_DIR HOST [PEAK_LEVEL]` | HTML в stdout | Чтение JSONL из `INCIDENT_LOG_DIR`; сам БД не пишет | Вызывается sampler-ом как подшаг (не отдельный timer) |
| `python -m telegram_bot --poll-once <env>` | `--poll-once`, env file; env: `TELEGRAM_*`, `COCK_MONITOR_HOME`, `MTPROXY_*` | Ответы на команды в Telegram | Polling `getUpdates`; `/status` через Python service (`cock_monitor.services.status_report`); `/chart` и `/vless_delta` через Python services; `/mt_*` операции с `METRICS_DB` | [`systemd/cock-monitor-telegram-bot.timer`](../systemd/cock-monitor-telegram-bot.timer) -> [`systemd/cock-monitor-telegram-bot.service`](../systemd/cock-monitor-telegram-bot.service) |

## 2) Контракт по `METRICS_DB`: таблицы, колонки, владельцы

| Таблица | Ключевые колонки | Владелец (owner) | Кто пишет | Кто читает |
|---|---|---|---|---|
| `cock_monitor_schema` | `component`, `version` | `cock_monitor.storage.migrations_conntrack_host` | миграции `conntrack_host` | storage при migrate |
| `conntrack_samples` | `ts`, `fill_pct`, `fill_count`, `fill_max`, `drop`, `insert_failed`, `early_drop`, `error`, `invalid`, `search_restart`, `interval_sec`, `delta_*` | `cock_monitor.storage.migrations_conntrack_host` + `conntrack-storage write-from-env` | `bin/check-conntrack.sh` (через `python -m cock_monitor conntrack-storage`) | `python -m cock_monitor daily-chart`, аналитика/ручные запросы |
| `host_samples` | `ts`, `load1`, `mem_avail_kb`, `swap_used_kb`, `tcp_*`, `shaper_rate_mbit`, `shaper_cpu_pct`, `tc_qdisc_root` | `cock_monitor.storage.migrations_conntrack_host` + `conntrack-storage write-from-env` | `bin/check-conntrack.sh` | `python -m cock_monitor daily-chart`, постмортем-диагностика |
| `vless_daily_snapshots` | PK `(snapshot_day_msk, email)`, `ts`, `up_bytes`, `down_bytes`, `total_bytes` | `cock_monitor.storage.vless_repository` | `python -m cock_monitor vless-report` | `python -m cock_monitor vless-report` |
| `vless_daily_reports` | `snapshot_day_msk`, `ts`, `total_clients`, `total_delta_bytes`, `top1_*`, `sent_ok` | `cock_monitor.storage.vless_repository` | `python -m cock_monitor vless-report` | отчётность/диагностика |
| `vless_report_checkpoints` | PK `(ts, email)`, `total_bytes`, `source` | `cock_monitor.storage.vless_repository` | `python -m cock_monitor vless-report` (`since-last-sent`) | `python -m cock_monitor vless-report` |
| `mtproxy_metrics` | `ts`, `total_connections`, `unique_ips`, `bytes_in`, `bytes_out`, `top_ips_json` | `mtproxy_module.repository` | `python -m cock_monitor mtproxy-collect` | `python -m cock_monitor mtproxy-daily`, `/mt_today`, `/mt_status` |
| `mtproxy_alerts` | `ts`, `alert_type`, `alert_key`, `message` | `mtproxy_module.repository` | `python -m cock_monitor mtproxy-collect` | alert cooldown logic, диагностика |
| `mtproxy_state` | `key`, `value` | `mtproxy_module.repository` | collector + `/mt_threshold` | collector + `/mt_*` |
| `mtproxy_ip_geo_cache` | `ip`, `data`, `ts` | `mtproxy_module.repository` | mtproxy module | mtproxy reports/status |

Вне `METRICS_DB`, но критично для VLESS: `XUI_DB_PATH` (read-only), таблицы 3x-ui (`client_traffics`, связанная схема).

## 3) Операционные зависимости (утилиты, права, пути, systemd)

### 3.1 Утилиты и runtime

- Обязательные базовые: `bash`, `python3`, `curl`, модуль Python `sqlite3`.
- По сценарию `conntrack`: `conntrack` (опционально, но нужен для stats-строки/alerts).
- По сценарию MTProxy: `ss`, `iptables`, `pgrep`.
- По сценарию shaper: `ip`, `tc` (и поддержка `sch_cake`).
- По сценарию incident sampler: `ping`, DNS резолв.
- Для графиков: `matplotlib` (daily chart, MTProxy daily, `/chart`, `/mt_today`).
- Для ops/ручной диагностики: `sqlite3` CLI рекомендован (не обязателен для runtime записи).

### 3.2 Права

- Для чтения `nf_conntrack` и `conntrack -S`: обычно root или соответствующие capabilities.
- Для `tc`/`ip` операций шейпера: root.
- Для `iptables`/`ss` в MTProxy collector: root или права на сетевой namespace.
- Для чтения `XUI_DB_PATH` и VLESS access.log: права чтения файлов.
- Для записи state/log/db файлов: права записи в каталоги `/var/lib/cock-monitor` и путь `METRICS_DB`.

### 3.3 Ключевые пути контрактов

- Env: `/etc/cock-monitor.env` (или кастомный путь аргументом).
- Runtime data: `/var/lib/cock-monitor` (`STATE_FILE`, `METRICS_DB`, incident JSONL, shaper state/status, Telegram offset).
- Deploy root: `/opt/cock-monitor`.
- Linux proc/net источники: `/proc/sys/net/netfilter/*`, `/proc/loadavg`, `/proc/net/dev`, `/proc/net/sockstat`, `/proc/meminfo`.

### 3.4 systemd units (операционный контракт)

- Conntrack monitor: `cock-monitor.timer` + `cock-monitor.service`.
- Telegram polling: `cock-monitor-telegram-bot.timer` + `cock-monitor-telegram-bot.service`.
- Daily conntrack chart: `cock-monitor-daily.timer` + `cock-monitor-daily.service`.
- VLESS daily: `cock-vless-daily.timer` + `cock-vless-daily.service`.
- MTProxy monitor: `cock-mtproxy-monitor.timer` + `cock-mtproxy-monitor.service`.
- MTProxy daily: `cock-mtproxy-daily.timer` + `cock-mtproxy-daily.service`.
- CPU shaper: `cock-shaper.timer` + `cock-shaper.service`.
- Incident sampler: `cock-monitor-incident-sampler.timer` + `cock-monitor-incident-sampler.service`.

## 4) Критичные пользовательские сценарии для регресса

1. Алерт conntrack fill (warning/critical), cooldown и эскалация.
2. STATS/delta/rate алерты с корректной дедупликацией по cooldown.
3. Dry-run `check-conntrack` не шлёт Telegram и не пишет в БД.
4. `/status` отдаёт полный текст статуса без зависания.
5. `/chart` строит PNG из `METRICS_DB` и отправляет фото.
6. VLESS daily и `/vless_delta` корректно считают дельты и checkpoint.
7. MTProxy collector пишет метрики/алерты; `/mt_status`, `/mt_today`, `/mt_threshold` работают.
8. Shaper корректно меняет rate, пишет state/status и не ломает сеть при `SHAPER_ENABLE=0/1`.
9. Incident sampler пишет JSONL, держит state, на recovery формирует post-mortem.

## 5) Явные ограничения Stage 0

- Это документирование текущего контракта "as-is", без изменения бизнес-логики.
- В `telegram_bot` продуктовые команды (`/status`, `/chart`, `/vless_delta`, `/mt_*`) выполняются через Python services/API; shell `cock-status.sh` остаётся как ops-утилита для ручной диагностики.
- Схема `METRICS_DB` распределена между несколькими модулями (conntrack/VLESS/MTProxy) в одном файле БД.
