## Отчёт по этапу 0

- Цель этапа: зафиксировать текущее поведение и операционные контракты до рефакторинга.
- Что сделано: обновлен `docs/stage-0-inventory-and-contracts.md` (матрица сценариев, контракт `METRICS_DB`, операционные зависимости, критичные regression-сценарии).
- Изменённые файлы: `docs/stage-0-inventory-and-contracts.md`.
- Breaking changes: нет.
- Миграции данных: нет.
- Обновления документации: `docs/stage-0-inventory-and-contracts.md`.
- Регресс-проверки:
  - pytest: ok (в `.venv`)
  - ruff: fail (на тот момент из-за pre-existing проблем вне scope этапа)
  - smoke-сценарии: частично fail из-за ограничений env/OS.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: e2e smoke требует Linux + реальные токены/данные.
- Готовность к следующему этапу: да.

## Отчёт по этапу 1

- Цель этапа: вывести orchestration `check-conntrack` из shell в Python use-case.
- Что сделано: добавлен Python use-case `run_conntrack_check`, shell-скрипт `bin/check-conntrack.sh` превращён в thin wrapper, удалён `eval`-контракт shell↔python, добавлен CLI `conntrack-check` и тесты.
- Изменённые файлы: `bin/check-conntrack.sh`, `cock_monitor/__main__.py`, `cock_monitor/conntrack_check_cli.py`, `cock_monitor/services/conntrack_check.py`, `tests/test_conntrack_check_cli.py`.
- Breaking changes: нет (внешний запуск через `bin/check-conntrack.sh` сохранён).
- Миграции данных: нет.
- Обновления документации: не требовались по внешнему контракту.
- Регресс-проверки:
  - pytest: ok
  - ruff: pass по stage-файлам; общий `ruff check .` на том шаге упирался в pre-existing проблемы
  - smoke-сценарии: частично ok, env-зависимые кейсы зафиксированы.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: полный smoke требует целевого окружения.
- Готовность к следующему этапу: да.

## Отчёт по этапу 2

- Цель этапа: единая модель транзакций и storage-границ.
- Что сделано: в `mtproxy_module/repository.py` введены явные transaction boundaries и composition-friendly mutate операции без внутренних commit; добавлены rollback/happy/migration тесты.
- Изменённые файлы: `mtproxy_module/repository.py`, `bin/cock-mtproxy-collect.py`, `tests/test_mtproxy_repository_transactions.py`, `docs/stage-2-transaction-boundaries.md`.
- Breaking changes: нет.
- Миграции данных: да (версионирование схемы MTProxy через `PRAGMA user_version=1`, совместимость проверена тестом).
- Обновления документации: `docs/stage-2-transaction-boundaries.md`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично ok, env/OS ограничения явно зафиксированы.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: e2e Telegram/Linux smoke отложен на целевой хост.
- Готовность к следующему этапу: да.

## Отчёт по этапу 3

- Цель этапа: CLI/entrypoints без `sys.path`-хака.
- Что сделано: удалены `sys.path`-инъекции в `bin/*.py`; добавлены пакетные CLI (`daily-chart`, `vless-report`, `mtproxy-collect`, `mtproxy-daily`); systemd/cron/docs переведены на `python -m ...`.
- Изменённые файлы: `bin/cock-daily-chart.py`, `bin/cock-vless-daily-report.py`, `bin/cock-mtproxy-collect.py`, `bin/cock-mtproxy-daily.py`, `cock_monitor/__main__.py`, `cock_monitor/daily_chart_cli.py`, `cock_monitor/mtproxy_collect_cli.py`, `cock_monitor/mtproxy_daily_cli.py`, `cock_monitor/services/vless_report.py`, `systemd/*` (релевантные unit), `README.md`, `config.example.env`, `examples/crontab`, `pyproject.toml`.
- Breaking changes: да (канонический operational запуск закреплён как `python -m ...`; thin wrappers оставлены).
- Миграции данных: нет.
- Обновления документации: `README.md`, `config.example.env`, `examples/crontab`, `docs/stage-0-inventory-and-contracts.md`, `systemd/*`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично ok, env-ограничения зафиксированы.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: финальный smoke на production-like Linux.
- Готовность к следующему этапу: да.

## Отчёт по этапу 4

- Цель этапа: убрать shell-зависимости Telegram-слоя.
- Что сделано: `/status` переведён на Python service; `telegram_bot/status_provider.py` больше не использует subprocess для продуктового сценария; унифицированы таймауты/ошибки для `/status`, `/chart`, `/vless_delta`, `/mt_*`.
- Изменённые файлы: `cock_monitor/services/status_report.py`, `telegram_bot/status_provider.py`, `telegram_bot/poll_once.py`, `telegram_bot/handlers.py`, `tests/test_status_provider.py`, `docs/stage-0-inventory-and-contracts.md`.
- Breaking changes: нет (внешнее поведение команд сохранено).
- Миграции данных: нет.
- Обновления документации: `docs/stage-0-inventory-and-contracts.md`, комментарии в `systemd/cock-monitor-telegram-bot.service`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично ok, e2e Telegram требует боевые токены/env.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: e2e проверки команд на целевом хосте.
- Готовность к следующему этапу: да.

## Отчёт по этапу 5

- Цель этапа: единые архитектурные границы для VLESS/Incident/Shaper.
- Что сделано: VLESS разбит на use-case + adapters + formatting; в incident sampler вынесены общие Linux host helpers; по shaper принято и задокументировано осознанное решение оставить shell.
- Изменённые файлы: `cock_monitor/services/vless_report_use_case.py`, `cock_monitor/adapters/vless_access_log.py`, `cock_monitor/adapters/vless_report_formatter.py`, `cock_monitor/services/vless_report.py`, `cock_monitor/adapters/linux_host.py`, `cock_monitor/services/incident_sampler.py`, `docs/stage-5-unified-boundaries.md`, `bin/cock-cpu-shaper.sh` (док-комментарий).
- Breaking changes: нет.
- Миграции данных: нет.
- Обновления документации: `docs/stage-5-unified-boundaries.md`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично ok, env/OS ограничения зафиксированы.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: e2e env-зависимые проверки на production-like сервере.
- Готовность к следующему этапу: да.

## Отчёт по этапу 6

- Цель этапа: финализация контракта и операционной модели.
- Что сделано: синхронизированы `README.md`, `DEPLOY.md`, `config.example.env`, `systemd/*`; обновлён `preflight`; добавлен upgrade guide для существующих инсталляций.
- Изменённые файлы: `README.md`, `DEPLOY.md`, `config.example.env`, `cock_monitor/preflight.py`, `systemd/cock-monitor.service`, `systemd/cock-monitor-incident-sampler.service` и релевантные unit после унификации entrypoint-контрактов.
- Breaking changes: да (канонический запуск unit закреплён через `python -m ...`).
- Миграции данных: нет (добавлены backup/upgrade шаги).
- Обновления документации: `README.md`, `DEPLOY.md`, `config.example.env`, `docs/stage-0-inventory-and-contracts.md`.
- Регресс-проверки:
  - pytest: ok
  - ruff: ok
  - smoke-сценарии: частично ok, Telegram e2e требует боевые токены и Linux окружение.
- Критерии готовности этапа: выполнены.
- Риски/хвосты: production e2e smoke (`/status`, `/chart`, `/vless_delta`, `/mt_*`) после выката.
- Готовность к следующему этапу: да.
