# Задачи: cock-monitor и расследование жалоб на качество VPN

Документ для передачи другому агенту. Задачи упорядочены по **приоритету** (сначала максимальный эффект при разумном объёме работ).

> **v2 (после фаз 7–13):** продуктовый core — Python, не bash. Периодический tick: `python -m cock_monitor run core` (`systemd/cock-monitor-core.timer`). Статус `/status` и `cock-status.sh` → `cock_monitor/services/status_report.py`. Запись в `METRICS_DB` (`conntrack_samples` + `host_samples`) — `cock_monitor/services/conntrack_check.py`. Шейпер: `run shaper` → `bin/cock-cpu-shaper.sh`. Incident JSONL: `run incident`. Ручной burst: `python -m cock_monitor burst-capture`.

---

## Контекст (зафиксировать при реализации)

На типичном маленьком VPS (пример из практики: **1 vCPU**, **~700 MiB RAM**, **Ubuntu 24.04**, **3x-ui + Xray** и параллельно **MTProto-proxy**) жалобы пользователей звучат как:

- периодические **обрывы**, нужно переподключаться;
- иногда **«ничего не грузит»** / сильные тормоза.

Наблюдаемые на хосте классы причин (не взаимоисключающие):

1. **`nf_conntrack`**: высокие счётчики **`drop` / `early_drop` / `insert_failed`** (накопительные), при пиках много **TCP inuse** и иногда заметный **`orphan`** в `/proc/net/sockstat`.
2. **Ресурсы**: мало RAM, использование **swap**, один CPU делят **Xray** и другие сервисы (например MTProxy).
3. **Нет исходящего шейпинга** на WAN (`tc` только `fq` без CAKE/HTB-лимита) — пики Mbps усиливают нагрузку на CPU/conntrack.
4. **Мониторинг не был включён**: таймеры **`cock-monitor-core.timer`** / **`cock-monitor-shaper.timer`** могли быть `inactive` — тогда **нет истории** в `METRICS_DB` для постмортема.
5. **Панель 3x-ui**: ошибки резолва **`api.telegram.org`** в `journalctl -u x-ui` — не то же самое, что пакетный VPN-туннель, но важно для «ощущения сбоя» и диагностики хоста.

Цель доработок cock-monitor: **быстро получать согласованный снимок и историю** (без полного `conntrack -L` и без раздувания диска), чтобы сопоставлять время жалобы с **conntrack / load / RAM / shaper / сетевые дропы**.

---

## Уже есть в репозитории (не дублировать с нуля)

| Компонент | Файлы | Заметки |
|-----------|--------|---------|
| Периодический core tick + Telegram | `cock_monitor/services/conntrack_check.py`, `modules/core/service.py` | fill `nf_conntrack`, опционально `conntrack -S`, SQLite `METRICS_DB`, алерты, дельты (`ALERT_ON_STATS_DELTA`, `STATS_RATE_*`) |
| systemd | `systemd/cock-monitor-core.*` | `ExecStart=python -m cock_monitor run core` |
| Текст статуса `/status` | `cock_monitor/services/status_report.py`, `modules/core/status.py` | Host snapshot: mem, load, sockstat, WAN `ip -s link`, `STATUS_EXTRA_UNITS`, conntrack, shaper block |
| Ручной статус | `bin/cock-status.sh` | Thin wrapper → `build_core_status()` (Python) |
| Legacy wrapper tick | `bin/check-conntrack.sh` | Thin wrapper → `run core` |
| `host_samples` в METRICS_DB | `storage/conntrack_host_repository.py`, `storage/migrations_conntrack_host.py` | load1, mem, swap, TCP sockstat, shaper rate/cpu, tc qdisc — пишет `conntrack_check` |
| Контекст в STATS-алертах | `conntrack_check._format_stats_host_context()` | load/mem/swap, sockstat TCP, shaper block при delta/cumulative alerts |
| CPU-aware шейпер | `bin/cock-cpu-shaper.sh`, `systemd/cock-monitor-shaper.*` | CAKE; gate через `ENABLED_MODULES=...,shaper` |
| Incident JSONL (10s) | `modules/incident/`, `systemd/cock-monitor-incident.*` | ping, DNS, conntrack, TCP probe, hop links |
| Burst capture (1 Hz, on-demand) | `services/burst_capture.py`, `burst-capture` CLI | JSONL при reconnect-storm; см. `docs/burst-diagnosis-london.md` |
| Shell lib (legacy ops) | `lib/conntrack-metrics.sh` | Defaults/helpers; **не** primary path для `/status` и METRICS_DB |
| Пример env | `config.example.env` | `STATUS_*`, `SHAPER_*`, `METRICS_*`, `STATS_ALERT_SHAPER_MAX_AGE_MIN` |
| Деплой | `DEPLOY.md`, `README.md`, `install/profiles.md` | v2 timers `cock-monitor-<module>.*`, `ENABLED_MODULES` |

---

## Задача P1 — Расширить статус `/status` ✅ выполнено

**Статус:** реализовано в Python (`build_status_report`).

**Файлы:** `cock_monitor/services/status_report.py` (env: `STATUS_WAN_IFACE`, `STATUS_IP_LINK_HEAD_LINES`, `STATUS_EXTRA_UNITS`, `SHAPER_STATUS_FILE`).

**Что есть:**

1. Память / swap из `/proc/meminfo` (`MemAvailable`, swap used/total).
2. Load average из `/proc/loadavg`.
3. TCP-свод из `/proc/net/sockstat` (`TCP:`, `TCP6:`).
4. WAN: `ip -s link show dev $STATUS_WAN_IFACE` (fallback на `SHAPER_IFACE`, default `ens3`).
5. `STATUS_EXTRA_UNITS` — `systemctl is-active` + `ActiveEnterTimestamp` per unit.
6. Conntrack fill + `conntrack -S` + блок VPN CPU Shaper.

**Проверка:** `bin/cock-status.sh /etc/cock-monitor.env` или `/status` в Telegram (`platform/telegram/dispatch.py` → `build_core_status`).

**Не дублировать:** правки в `lib/conntrack-metrics.sh` для продуктового `/status` — только если нужен чисто shell-ops путь без Python.

---

## Задача P2 — Расширить записи в `METRICS_DB` ✅ выполнено

**Статус:** таблица `host_samples` + запись на каждом core tick.

**Файлы:**

- `cock_monitor/storage/migrations_conntrack_host.py` — DDL `host_samples`
- `cock_monitor/storage/conntrack_host_repository.py` — INSERT/prune
- `cock_monitor/services/conntrack_check.py` — `_collect_host_sample()`, пишет вместе с `conntrack_samples` (общий `ts`)

**Поля:** `ts`, `load1`, `mem_avail_kb`, `swap_used_kb`, `tcp_inuse`, `tcp_orphan`, `tcp_tw`, `tcp6_inuse`, `shaper_rate_mbit`, `shaper_cpu_pct`, `tc_qdisc_root`.

**Retention:** общий `METRICS_RETENTION_DAYS` / prune в repository.

**Пример запроса:**

```sql
SELECT datetime(ts, 'unixepoch') AS t, load1, mem_avail_kb, swap_used_kb, tcp_inuse, tcp_orphan
FROM host_samples
WHERE ts BETWEEN strftime('%s', '2026-06-27 10:00') AND strftime('%s', '2026-06-27 11:00')
ORDER BY ts;
```

---

## Задача P3 — Обогатить Telegram при `ALERT_ON_STATS_DELTA` ✅ выполнено

**Статус:** контекстный блок в алертах через `_format_stats_host_context()`.

**Файл:** `cock_monitor/services/conntrack_check.py`

**Что добавляется в алерт (3–4 строки):** `load1`, `MemAvailable`, swap used; строка `sockstat TCP:`; `shaper: <rate> cpu=<pct>%` или `shaper: no data` (свежесть по `STATS_ALERT_SHAPER_MAX_AGE_MIN` и `ts=` в `SHAPER_STATUS_FILE`).

**Проверка:** `python -m cock_monitor run core /path/to.env --dry-run` с заниженными порогами delta в тестовом `.env`.

---

## Задача P4 — Предупреждение: критичные systemd-таймеры не активны ⏳ открыто

**Цель:** не оставаться без данных «по умолчанию», если забыли `enable --now`.

**Предлагаемые файлы:** `cock_monitor/services/status_report.py` (блок в конце отчёта) и/или `conntrack_check.py` (stderr или редкий Telegram с cooldown).

**Поведение (v2 unit names):**

- Проверять `systemctl is-active cock-monitor-core.timer`.
- Если `shaper` ∈ `ENABLED_MODULES` — `cock-monitor-shaper.timer`.
- Опционально: `cock-monitor-incident.timer`, `cock-monitor-telegram.timer` — через env `STATUS_WARN_TIMERS` (список).
- Вывод в `/status`: `WARN: cock-monitor-core.timer inactive` и т.д.

**Критерий готовности:** на хосте без включённого таймера в `/status` явно видно предупреждение; на нормально настроенном — нейтрально или без строки.

---

## Задача P5 — Снимок при инциденте ⏳ частично

**Цель:** по жалобе собрать зафиксированный артефакт без ручного набора команд.

**Уже есть (не дублировать с нуля):**

| Инструмент | Когда использовать |
|------------|-------------------|
| `python -m cock_monitor burst-capture start --duration N` | Короткий reconnect-storm / burst (1 Hz JSONL + access log tail) |
| `run incident` + `incident-status` | Непрерывная история 10s JSONL (`/var/lib/cock-monitor/incident-*.jsonl`) |
| `bin/incident-postmortem.py` | Разбор incident JSONL за окно |

**Опционально открыто:** одноразовый «ops snapshot» в текстовый файл (`/var/lib/cock-monitor/incident-last.txt`) с host block из P1 + усечённый `journalctl -u x-ui -n 20` — если нужен именно **перезаписываемый .txt**, а не JSONL. Новый файл: например `bin/cock-incident-snapshot.sh` (wrapper) или subcommand в `cock_monitor`.

**Запрещено:** `conntrack -L`, полный `iptables-save`, безлимитный journal.

---

## Задача P6 — Документация: плейбук расследования ⏳ частично

**Цель:** человек без памяти «что смотреть первым».

**Уже есть:** `docs/burst-diagnosis-london.md` (burst + incident), разделы README (incident sampler, burst-capture).

**Открыто:** единый `docs/vpn-incident-playbook.md` со ссылкой из README:

1. `/status` или `cock-status.sh`
2. SQL по `conntrack_samples` / `host_samples` на интервал жалобы
3. incident JSONL / burst-capture report
4. shaper status file, WAN drops
5. x-ui journal vs VPN-трафик; внешние факторы (MTProxy `-c`, PID>65535 — см. `examples/mtproto-proxy-exec-args.example`)

---

## Задача P7 — (Опционально) Метрики панели / DNS ⏳ открыто

**Цель:** не смешивать с ядром VPN, но фиксировать «хост тупит наружу».

**Идеи:** раз в N минут `getent` / `curl --connect-timeout 2` при `EXTERNAL_PROBE_ENABLE=1` — в `host_samples` или одна строка в статусе.

**Риски:** лишние исходящие запросы, ложные тревоги. **P7 после** P4.

---

## Общие ограничения для всех задач

- **Не коммитить** реальные токены, MTProto `-S`, пути к чужим volume id — только плейсхолдеры в `config.example.env`.
- **Не раздувать диск:** приоритет journald / retention; новые файлы — перезапись или ротация.
- **Обратная совместимость:** старые `.env` без новых переменных должны работать (дефолты).
- **Primary path — Python core**; bash-обёртки (`bin/check-conntrack.sh`, `bin/cock-status.sh`) только делегируют в `python -m cock_monitor`.
- После изменений — кратко обновить `config.example.env` и при необходимости `DEPLOY.md`.

---

## Порядок выполнения для агента

| Приоритет | Задача | Статус |
|-----------|--------|--------|
| — | P1 статус | ✅ |
| — | P2 `host_samples` | ✅ |
| — | P3 контекст в STATS-алертах | ✅ |
| 1 | **P4** предупреждение о неактивных таймерах | открыто |
| 2 | **P5** ops snapshot (если нужен поверх burst/incident) | частично |
| 3 | **P6** единый playbook | частично |
| 4 | **P7** external probe | опционально |
