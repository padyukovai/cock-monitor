# Этап 5: единые архитектурные границы (VLESS / Incident / Shaper)

## Слои (v2)

| Слой | Назначение |
|------|------------|
| `cock_monitor/modules/*` | Модуль: `register.py` (registry), `service.py` / tick, `telegram_handlers.py`, domain-specific code |
| `cock_monitor/platform/*` | Registry, install, shared env (`env_runtime`), Telegram dispatch shell |
| `cock_monitor/services/*` | **Shared kernel** — use-cases для core conntrack и VLESS (исторически до полной упаковки в modules) |
| `cock_monitor/adapters/*` | OS / внешние системы (proc, sqlite, xui) |
| `cock_monitor/storage/*` | SQLite repositories и миграции |

`python -m cock_monitor run <module>` и Telegram-команды маршрутизируются через `ModuleSpec.run_tick` и `TelegramCommand.handler` в registry — не через отдельные таблицы в `run_cli` / `dispatch.py`.

## Ownership и flow после изменений

### VLESS

- **module orchestration:** `cock_monitor/modules/vless/` (`register.py`, `telegram_handlers.py`, `run_vless_daily_tick`)
- **shared kernel (domain):** `cock_monitor/services/vless_report_use_case.py`
- adapters:
  - `cock_monitor/adapters/xui_sqlite.py` — чтение источника 3x-ui (`client_traffics`, inbounds VLESS)
  - `cock_monitor/adapters/vless_access_log.py` — извлечение IP-агрегаций из access.log окна
  - `cock_monitor/adapters/vless_report_formatter.py` — форматирование текста отчета
  - `cock_monitor/storage/vless_repository.py` — snapshots/checkpoints/meta в `METRICS_DB`
- entrypoint:
  - `python -m cock_monitor run vless` → `modules/vless/telegram_handlers.run_vless_daily_tick`
  - CLI: `cock_monitor/services/vless_report.py` (thin wrapper)

Flow: CLI/handler -> use-case -> adapters/storage -> Telegram(optional).

### Incident sampler

- owner module: `cock_monitor/modules/incident/`
  - `sampler.py` — tick orchestration (`run_once`)
  - `probes.py` — ping, DNS, TCP probe, conntrack, hop links, systemd
  - `level.py` — severity from probe readings
  - `postmortem.py` — state, Telegram alerts, JSONL line format
  - `service.py` — entry for `python -m cock_monitor run incident`
- shared host helpers:
  - `cock_monitor/adapters/linux_host.py`:
    - `read_hostname_fqdn()`
    - `read_sysctl_int()`
    - `read_conntrack_fill()`
    - `read_load_mem_from_proc()`, `parse_ss_state_line_counts()`

Flow: `run incident` -> service -> sampler -> probes/level -> JSONL/state -> Telegram(optional).

### Shaper

- owner scenario: `bin/cock-cpu-shaper.sh`
- статус решения: shell оставлен осознанно.

Причины:

1. управление идет через `tc/ip` и точечные shell-команды;
2. метрики CPU берутся из `/proc/stat` в коротком цикле;
3. Python-порт сейчас не уменьшит I/O-поверхность (все равно shell-out в те же утилиты), но добавит прослойку и операционный риск.

Зафиксировано в header-комментарии `bin/cock-cpu-shaper.sh`.

## Отчёт по этапу 5

- Цель этапа: довести VLESS/Incident/Shaper до единых архитектурных границ (owner + flow, меньше cross-import/дублирования).
- Что сделано:
  - VLESS: выделен owner use-case и разнесены адаптеры access-log/formatting.
  - Incident: вынесены общие host helpers в `adapters/linux_host.py`, удалено локальное дублирование.
  - Shaper: оставлен на shell с явной архитектурной мотивацией и документацией причин.
- Изменённые файлы:
  - `cock_monitor/services/vless_report.py`
  - `cock_monitor/services/vless_report_use_case.py` (new)
  - `cock_monitor/adapters/vless_access_log.py` (new)
  - `cock_monitor/adapters/vless_report_formatter.py` (new)
  - `cock_monitor/adapters/linux_host.py`
  - `cock_monitor/modules/incident/` (sampler, probes, level, postmortem)
  - `docs/stage-5-unified-boundaries.md` (new)
- Breaking changes: нет.
- Миграции данных: нет.
- Обновления документации: добавлен документ этапа `docs/stage-5-unified-boundaries.md`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично (см. отчет в ответе агента с причинами/окружением)
- Критерии готовности этапа: выполнены.
- Риски/хвосты:
  - smoke в Linux/production runtime должен быть перепроверен на целевом хосте с реальными `/proc`, `conntrack`, Telegram credentials.
- Готовность к следующему этапу: да.
