# План рефакторинга cock-monitor (для поэтапного выполнения агентами)

Документ задаёт **общий контекст**, **целевое направление** и **последовательные этапы** рефакторинга. Его можно использовать как единственный входной артефакт для ИИ-агента: выполнять **строго по порядку**, один этап за раз, с фиксацией критериев готовности.

Breaking changes **допустимы**, если они осознанно зафиксированы в этапе и обновлены `README.md` / `DEPLOY.md` / `config.example.env` / unit-файлы.

---

## 1. Назначение продукта (кратко)

**cock-monitor** — набор скриптов для Linux VPS:

- мониторинг заполнения **`nf_conntrack`** и опционально счётчиков **`conntrack -S`**;
- алерты в **Telegram** по расписанию (**systemd timer** / cron), без постоянного демона;
- опционально: история в **SQLite** (`METRICS_DB`), суточные графики (**matplotlib**), отчёты по **VLESS/3x-ui**, **CPU-aware шейпер** (`tc`/CAKE), **incident sampler** + post-mortem;
- опционально: модуль **MTProxy** (`mtproxy_module`) и команды Telegram-бота (`telegram_bot`).

Типичный деплой: код в `/opt/cock-monitor`, секреты в `/etc/cock-monitor.env`, переменная окружения `COCK_MONITOR_HOME`, таймеры из `systemd/`. Подробности: [`README.md`](../README.md), [`DEPLOY.md`](../DEPLOY.md), шаблон [`config.example.env`](../config.example.env).

---

## 2. Текущая структура репозитория (ориентиры)

| Область | Пути | Роль |
|--------|------|------|
| Conntrack + метрики + алерты | `bin/check-conntrack.sh`, `lib/conntrack-metrics.sh` | Основной «god script» и библиотека defaults/форматирования статуса |
| Ручной статус | `bin/cock-status.sh` | Обёртка над `format_full_status_text` |
| Суточный график | `bin/cock-daily-chart.py` | PNG из `METRICS_DB` |
| VLESS | `bin/cock-vless-daily-report.py` | Чтение БД 3x-ui, снапшоты в `METRICS_DB`, Telegram |
| Шейпер | `bin/cock-cpu-shaper.sh`, `systemd/cock-shaper.*` | CAKE/HTB, status-файл |
| Инциденты | `bin/incident-sampler.sh`, `bin/incident-postmortem.py`, `lib/incident-metrics.sh` | JSONL, постмортем |
| Telegram (polling) | `telegram_bot/*` | Разовый опрос `getUpdates` по таймеру; команды `/status`, `/chart`, `/vless_delta`, `/mt_*` |
| MTProxy | `mtproxy_module/core.py`, `mtproxy_module/charts.py`, `bin/cock-mtproxy-*.py` | Сбор, SQLite, алерты, графики |
| Юниты | `systemd/*.service`, `systemd/*.timer` | Расписание запусков |
| Миграция сервера (не ядро продукта) | `migration/` | Инструкции и артефакты переноса; см. [`migration/migration.md`](../migration/migration.md) |

**Пересечение данных:** несколько подсистем пишут в **один** `METRICS_DB` (таблицы conntrack/host, VLESS, MTProxy и т.д.). Схема создаётся «на лету» (`CREATE TABLE IF NOT EXISTS`) в разных скриптах — это главный источник связности и рисков при эволюции.

---

## 3. Диагностика: что не так с архитектурой сейчас

1. **God script:** `bin/check-conntrack.sh` совмещает доменные правила (пороги, cooldown, escalation), работу с SQLite, retention, формирование текста и отправку Telegram.
2. **Смешение слоёв:** `mtproxy_module/core.py` — сбор через `ss`/`iptables`, geo HTTP, SQLite, форматирование сообщений в одном модуле.
3. **Неявные контракты:** `telegram_bot/handlers.py` оркестрирует логику через `subprocess.run` к другим скриптам (`cock-daily-chart.py`, `cock-vless-daily-report.py`) — сложно тестировать и единообразно обрабатывать ошибки.
4. **Дублирование:** парсинг `.env` (`_parse_env_file` и аналоги) в нескольких файлах (`telegram_bot/config.py`, `bin/cock-*.py`).
5. **Нет единой модели зависимостей Python** (кроме `requirements-chart.txt` для matplotlib) — воспроизводимость окружения слабая.
6. **Нет или почти нет автотестов** на критичные правила (cooldown, дельты, rate, пороги).
7. **Операционные допущения** (root, `/opt`, набор CLI-утилит) размазаны по коду без централизованного preflight.

---

## 4. Целевая архитектура (высокий уровень)

Цель рефакторинга — не «переписать всё», а получить **устойчивые границы**:

```
domain/          # чистая логика: пороги, cooldown, дельты/rate, модели событий (без I/O)
adapters/        # ОС: /proc, conntrack, ss, iptables, sqlite, telegram API, HTTP
services/        # сценарии: «один прогон проверки», «суточный отчёт», «команда бота»
interfaces/      # тонкие entrypoints: CLI (bin), systemd-вызовы, telegram_bot
```

Принципы для агентов:

- **Домен** не импортирует `subprocess`, `sqlite3`, `urllib` — только типы и чистые функции.
- **Тонкие оболочки** в `bin/` (bash или `python -m`) — парсинг argv/env и вызов сервиса.
- **Один источник правды** для конфигурации: загрузка + валидация + значения по умолчанию.
- **SQLite:** явные репозитории/DAO + versioned migrations (или минимум `schema_version` + миграционные шаги), без размазанного DDL по десяти файлам без учёта версии.

---

## 5. Как выполнять этапы (правила для агента)

1. **Берите один этап** из раздела 6; не смешивайте цели разных этапов в одном PR/коммите, если это не микро-правка.
2. **Перед начала этапа:** прочитайте перечисленные файлы и критерии готовности.
3. **После этапа:** обновите пользовательскую документацию (`README.md`, `DEPLOY.md`, `config.example.env`) при изменении контрактов.
4. **Регрессии:** прогоните ручные проверки из этапа; при добавлении тестов — зафиксируйте команду запуска в `README.md` или в этом документе в конце этапа.
5. **Секреты:** не коммитьте реальные `.env`; `migration/source-configs` может содержать чувствительные данные — не копировать в репозиторий новые секреты.

---

## 6. Этапы рефакторинга (порядок фиксирован)

### Этап 0 — Инвентаризация и «контракты на бумаге»

**Цель:** зафиксировать публичные поведения до ломки внутренностей.

**Действия:**

- Составить **краткую матрицу**: сценарий → входы (env, CLI) → выходы (Telegram, файлы, SQLite-таблицы) → таймеры.
- Зафиксировать список таблиц/ключевых колонок в `METRICS_DB` (по коду и `README.md`).

**Критерий готовности:** в репозитории есть отдельный подраздел (можно в конце этого файла или в `README.md`) «Сценарии и данные» — или агент добавляет приложение **A** в конец `docs/refactoring-plan.md` с таблицей сценариев.

Результат инвентаризации Этапа 0: [`docs/stage-0-inventory-and-contracts.md`](stage-0-inventory-and-contracts.md).

**Файлы:** `README.md`, `config.example.env`, скрипты из `bin/`, `mtproxy_module/core.py`.

---

### Этап 1 — Единый конфиг и preflight

**Цель:** один модуль загрузки/валидации `.env` + явная проверка окружения (бинарники, права, пути).

**Действия:**

- Вынести общий парсер `.env` (сейчас дублируется в `telegram_bot/config.py` и `bin/cock-*.py`).
- Добавить функцию/CLI `preflight` (или флаг `--check`) проверки: `sqlite3`, `conntrack`, `curl`, `matplotlib` (если нужен график), и т.д. по сценарию.
- Централизовать defaults в одном месте (или сгенерировать из одной схемы), не ломая смысл текущих переменных.

**Критерий готовности:**

- Все Python entrypoints используют один и тот же парсер/слой конфигурации.
- Документирована команда проверки окружения перед деплоем.
- Регресс: `cock-status.sh`, `check-conntrack.sh --dry-run`, один запуск бота `python3 -m telegram_bot --poll-once` (как в README) — работают.

**Файлы:** `telegram_bot/config.py`, `bin/cock-daily-chart.py`, `bin/cock-vless-daily-report.py`, `bin/cock-mtproxy-collect.py`, `bin/cock-mtproxy-daily.py`, при необходимости новый пакет `cock_monitor/` или `lib/`.

---

### Этап 2 — Выделение домена из `check-conntrack.sh` (итерация 1)

**Цель:** уменьшить god script: вынести **чистую** логику решений (алерты, cooldown, severity) в тестируемый Python-модуль; shell оставить как thin wrapper **или** заменить entrypoint на `python -m`.

**Действия:**

- Идентифицировать функции: расчёт fill%, правила `should_send_*`, логика STATS cumulative/delta/rate (как задокументировано в README).
- Перенести в Python функции с чёткими входами/выходами (без глобального состояния).
- Интеграция: bash вызывает Python для решения «слать/не слать» и текста сообщения, либо весь `main` в Python.

**Критерий готовности:**

- Минимум 5–10 unit-тестов на домен (cooldown, escalation warning→critical, граничные значения порогов).
- Поведение для пользователя при том же `.env` сохраняется (см. матрицу этапа 0).
- `bin/check-conntrack.sh` сокращён по ответственности или заменён на явный Python entrypoint с обновлённым `systemd/cock-monitor.service`.

**Файлы:** `bin/check-conntrack.sh`, `lib/conntrack-metrics.sh` (минимизировать дублирование с новым доменом).

---

### Этап 3 — Репозиторий SQLite и миграции для conntrack/host

**Цель:** вынести DDL/DML из `check-conntrack.sh` в отдельный слой с **версией схемы** и миграциями.

**Действия:**

- Ввести `schema_version` (таблица или PRAGMA user_version) и скрипты миграции «вверх».
- Инкапсулировать INSERT/retention/trim в классах функций.
- Не ломать существующие таблицы без миграции; при breaking — bump версии и инструкция в README.

**Критерий готовности:**

- Все операции с `conntrack_samples` / `host_samples` идут через один модуль.
- Тесты на миграцию с пустой БД и на существующей БД (фикстура).
- Документирован формат версионирования.

**Файлы:** новый модуль storage + текущий код из `bin/check-conntrack.sh`.

---

### Этап 4 — Разрезание `mtproxy_module/core.py`

**Цель:** разделить сбор, политику алертов, хранение, geo, форматирование.

**Действия:**

- `collector.py` — `ss`, `iptables`, `pgrep`, чтение `/proc`.
- `repository.py` / `state.py` — SQLite-операции и пороги в `mtproxy_state`.
- `geo.py` — HTTP кэш и TTL.
- `formatting.py` или `reports.py` — тексты и captions.
- `core.py` — тонкая сборка или удаление в пользу `services/mtproxy.py`.

**Критерий готовности:**

- Публичные entrypoints (`bin/cock-mtproxy-collect.py`, `bin/cock-mtproxy-daily.py`, `telegram_bot`) обновлены на новые импорты.
- Небольшие unit-тесты на парсинг `ss`/`iptables` (фикстуры stdout) и на cooldown алертов.

**Файлы:** `mtproxy_module/core.py`, `mtproxy_module/charts.py`, `bin/cock-mtproxy-*.py`.

---

### Этап 5 — Telegram: убрать subprocess из `handlers.py`

**Цель:** команды бота вызывают **сервисы** напрямую (Python API), а не внешние скрипты.

**Действия:**

- Для `/chart` — вызвать функцию генерации графика из того же кода, что использует `cock-daily-chart.py` (общий сервис).
- Для `/vless_delta` — вызвать use-case «отчёт since-last-sent» без `subprocess`.
- Единый контракт ошибок и таймаутов.

**Критерий готовности:**

- В `telegram_bot/handlers.py` нет `subprocess.run` для сценариев chart/vless (допускается временно для редких legacy путей, но с явным TODO и сроком).
- Регресс: `/chart`, `/vless_delta`, `/mt_*` по тестовому `.env` (или dry-run режимами скриптов).

**Файлы:** `telegram_bot/handlers.py`, `bin/cock-daily-chart.py`, `bin/cock-vless-daily-report.py`, возможно `telegram_bot/status_provider.py`.

---

### Этап 6 — Остальные сценарии: VLESS, шейпер, incident sampler

**Цель:** привести к тем же правилам: domain/adapters/services, меньше логики в shell.

**Действия по приоритету:**

1. `cock-vless-daily-report.py` — разделить чтение x-ui DB, агрегацию, Telegram.
2. `incident-sampler.sh` / `incident-postmortem.py` — минимизировать дублирование с host metrics; вынести парсинг в Python при необходимости.
3. `cock-cpu-shaper.sh` — оставить shell только как обёртку над `tc` или документировать, почему shell остаётся.

**Критерий готовности:** для каждого сценария есть один «owner»-модуль и чёткие тесты/ручные проверки из README.

---

### Этап 7 — Зависимости Python и качество

**Цель:** воспроизводимая установка и CI-гейт.

**Действия:**

- Ввести `pyproject.toml` или единый `requirements.txt` + опционально `requirements-dev.txt` (pytest, ruff/mypy по желанию).
- Минимальный CI: линтер + pytest на тестовом наборе.

**Критерий готовности:** в README описана установка dev-зависимостей и команда `pytest`.

---

### Этап 8 — Репозиторий и операционный шум

**Цель:** `migration/` не мешает продуктовой разработке.

**Действия:**

- Явно пометить в `migration/README.md` (или в начале `migration.md`), что каталог — архив/операционные артефакты, не часть runtime API.
- Рассмотреть вынесение в отдельный приватный репозиторий или git submodule (опционально).

**Критерий готовности:** новый разработчик не путает «код монитора» и «артефакты миграции сервера».

---

## 7. Риски и как их снижать

| Риск | Митигация |
|------|-----------|
| Регресс алертов (лишние/мало сообщений) | Этап 0 + golden-тесты на домен + `--dry-run` сравнение текстов |
| Потеря данных SQLite | Миграции с бэкапом файла в инструкции; `PRAGMA`/`user_version` |
| Сложность деплоя | Сохранить совместимость путей в systemd или дать drop-in пример |
| Раздувание абстракций | Каждый этап имеет «границу»; не вводить DI-фреймворки без необходимости |

---

## 8. Связанные документы

- [`docs/stage-0-inventory-and-contracts.md`](stage-0-inventory-and-contracts.md) — Этап 0: матрица сценариев, `METRICS_DB`, персистентные контракты.
- [`README.md`](../README.md) — пользовательская модель и переменные.
- [`DEPLOY.md`](../DEPLOY.md) — установка и systemd.
- [`config.example.env`](../config.example.env) — полный контракт env.
- [`docs/tasks-vpn-quality-investigation.md`](tasks-vpn-quality-investigation.md) — продуктовые задачи по диагностике VPN (не путать с этим планом рефакторинга).
- [`migration/migration.md`](../migration/migration.md) — миграция сервера x-ui/MTProxy; секреты не коммитить.

---

## Приложение A — шаблон для агента после каждого этапа

Скопируйте и заполните:

```markdown
## Отчёт по этапу N

- Сделано: [список]
- Изменённые файлы: [список]
- Критерии этапа: [да/нет по пунктам]
- Регресс: [команды и результат]
- Следующий этап: N+1 (готовность: да/нет)
```
