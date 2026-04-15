# Этап 5: единые архитектурные границы (VLESS / Incident / Shaper)

## Ownership и flow после изменений

### VLESS

- owner use-case: `cock_monitor/services/vless_report_use_case.py`
- adapters:
  - `cock_monitor/adapters/xui_sqlite.py` — чтение источника 3x-ui (`client_traffics`, inbounds VLESS)
  - `cock_monitor/adapters/vless_access_log.py` — извлечение IP-агрегаций из access.log окна
  - `cock_monitor/adapters/vless_report_formatter.py` — форматирование текста отчета
  - `cock_monitor/storage/vless_repository.py` — snapshots/checkpoints/meta в `METRICS_DB`
- entrypoint:
  - `cock_monitor/services/vless_report.py` — thin CLI wrapper + backward-compatible API

Flow: CLI/handler -> use-case -> adapters/storage -> Telegram(optional).

### Incident sampler

- owner scenario: `cock_monitor/services/incident_sampler.py`
- shared host helpers:
  - `cock_monitor/adapters/linux_host.py`:
    - `read_hostname_fqdn()`
    - `read_sysctl_int()`
    - `read_conntrack_fill()`
    - существующие `read_load_mem_from_proc()`, `parse_ss_tan_state_counts()`

Flow: sampler -> linux_host helpers + probes -> JSONL/state -> Telegram(optional).

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
  - `cock_monitor/services/incident_sampler.py`
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
