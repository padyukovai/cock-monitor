# План улучшения модульной архитектуры cock-monitor v2

Документ для **поэтапного выполнения одним ИИ-агентом за итерацию**.  
Предполагается, что базовый рефакторинг v2 (этапы 0–6 из [`refactoring-plan.md`](refactoring-plan.md)) уже выполнен: есть `ModuleRegistry`, профили, `ENABLED_MODULES`, модульные systemd timers.

Этот план закрывает разрыв между **задуманной модульностью** и **операционной реальностью**: dual enable flags, дублирование hop/incident, неполный install, ручные post-install шаги, legacy-артефакты.

---

## Контекст: бизнес-роли VPS

| Хост | Роль | Целевой набор модулей |
|------|------|----------------------|
| **RF3** | hop-gateway: мониторинг клиентского трафика + алертинг VLESS-тоннелей до DE/US | `core`, `hop` (+ опционально `vless`, `incident`) |
| **Germany, USA** | exit-node: мониторинг конечных 3x-ui узлов | `core`, `vless`, `shaper` (+ опционально `incident`) |
| **Helsinki** | mtproxy-only | `core`, `mtproxy` |
| **RF1** | минимальный хост-мониторинг | `core`, `incident` |
| **RF2** | WireGuard relay | `core`, `wg`, `incident` |

Принцип: **на каждом VPS только нужные модули**, без скрытого функционала «модуль включён, но флаг выключен».

---

## Правила выполнения для агента

1. Выполнять **строго одну фазу** за итерацию.
2. Перед началом — прочитать scope, «не трогать», критерии готовности.
3. После фазы — заполнить отчёт по шаблону из [`refactoring-plan.md`](refactoring-plan.md) (приложение A) в [`refactoring-stage-reports.md`](refactoring-stage-reports.md).
4. Breaking changes допустимы, если описаны в отчёте и обновлены `README.md`, `DEPLOY.md`, `install/profiles.md`, `config.example.env` (по затронутым контрактам).
5. Регресс-минимум: `pytest`, `ruff check .`, smoke `python -m cock_monitor run <module> --dry-run`.

---

## Принцип: без legacy и обратной совместимости

Проект маленький и целевой — **один канонический путь** для каждой функции. Не делаем:

- deprecated fallback в коде (`resolve_*` с чтением старых env-ключей);
- stderr-warning «старый ключ ещё работает»;
- thin wrapper «на один релиз»;
- дублирующие systemd units / entrypoints (v1 и v2 параллельно в docs).

**Делаем:** удаляем legacy-ключи, units, shim-пакеты и скрипты; обновляем docs как breaking; redeploy через `install/uninstall.sh`.

Удалённые env-флаги (не читать, не писать): `MTPROXY_ENABLE`, `INCIDENT_SAMPLER_ENABLE`, `SHAPER_ENABLE`, `INCIDENT_HOP_LINKS`.  
Единственный switch модулей: **`ENABLED_MODULES`**. Hop-линки: **`HOP_LINKS`** only.

---

## Фаза 7 — Единый источник включения модулей

### Цели фазы

Устранить рассинхрон **v2 `ENABLED_MODULES`** и **удалённых legacy-флагов** (`SHAPER_ENABLE`, `INCIDENT_SAMPLER_ENABLE`, `MTPROXY_ENABLE`). Сейчас timer модуля может крутиться, а логика — no-op. **Без fallback:** старые ключи не читаются.

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **Shaper:** активен iff `shaper` ∈ `ENABLED_MODULES` | `bin/cock-cpu-shaper.sh`, `config/fragments/shaper.env` | Timer и логика совпадают |
| **Incident / MTProxy:** `module_enabled()` only | `incident_sampler.py`, `modules/mtproxy/config.py` | Один источник правды |
| **Удалить** legacy env из example/fragments/install | `config.example.env`, `install-ubuntu-minimal.sh`, `configure_cli` (strip on apply) | Нет двух способов включения |
| **Docs:** только `ENABLED_MODULES` | `README.md`, `docs/v2-migration.md` | Onboarding без путаницы |

### Scope (границы)

- **В scope:** логика enable/disable, фрагменты, docs, тесты на новое поведение.
- **Вне scope:** перенос incident в `modules/`, hop-конфиг, install daily timers.

### Критерии готовности

- [x] `stack-3xui` после install + `run shaper --dry-run` показывает активный shaper (не «disabled SHAPER_ENABLE=0»).
- [x] `run incident` работает при `ENABLED_MODULES=...incident` без `INCIDENT_SAMPLER_ENABLE=1`.
- [x] `run mtproxy` работает при `ENABLED_MODULES=...mtproxy` без `MTPROXY_ENABLE=1`.
- [x] Тесты покрывают enable-логику shaper/incident/mtproxy.
- [ ] `pytest` и `ruff` проходят (на хосте с dev deps).

### Оценка объёма

~6–10 файлов, 1 агент.

---

## Фаза 8 — Install: daily timers и матрица профилей

### Цели фазы

Закрыть дыры v2 install: **daily-отчёты не ставятся**, хотя `ModuleSpec.daily_timer` уже есть. Исправить **документированную матрицу VPS** (Helsinki → `stack-mtproxy`, не `stack-3xui`).

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **Install daily timers** из `ModuleSpec.daily_timer` и явного списка для core/mtproxy | `cock_monitor/install_cli.py`, `cock_monitor/modules/core/register.py`, `cock_monitor/modules/mtproxy/register.py`, `cock_monitor/modules/vless/register.py` | vless daily, core chart, mtproxy daily ставятся автоматически при включённом модуле |
| **Маппинг daily unit → runner:** core → `run core --daily-chart`, vless → `run vless`, mtproxy → `mtproxy-daily` CLI | `cock_monitor/run_cli.py` или отдельный `platform/daily_runners.py` | Единая точка для install и systemd unit |
| **Helsinki и матрица профилей** | `install/profiles.md`, `DEPLOY.md` | Оператор деплоит правильный минимальный стек |
| **Опционально:** `stack-exit-node.env` как alias/копия `stack-3xui` с явным именем роли | `config/profiles/stack-exit-node.env` | Читаемость «DE/US = exit-node» без смены поведения |

### Scope

- **В scope:** install_cli, register.py (daily_timer flags), profiles.md, DEPLOY.md, systemd unit names (существующие `cock-*-daily.*`).
- **Вне scope:** hop/incident рефакторинг, post-install hooks.

### Критерии готовности

- [x] `install --profile stack-3xui` ставит `cock-vless-daily.timer` и `cock-monitor-daily.timer`.
- [x] `install --profile stack-mtproxy` ставит `cock-mtproxy-daily.timer` (если daily нужен mtproxy).
- [x] `install/profiles.md`: Helsinki → `stack-mtproxy`.
- [x] Тест install_cli (mock filesystem): enabled modules → ожидаемый набор timers.
- [ ] `pytest`, `ruff` OK (на хосте с dev deps).

### Оценка объёма

~8–12 файлов, 1 агент.

---

## Фаза 9 — Hop-конфиг и разделение алертинга hop vs incident

### Цели фазы

На RF3 модули `hop` и `incident` **дублируют мониторинг одних тоннелей** (разные пороги, два пути в Telegram). Нужно: **`HOP_LINKS` only**, hop-алерты только из `hop`, incident — JSONL/post-mortem.

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **`HOP_LINKS` — единственный ключ**; `INCIDENT_HOP_LINKS` удалён | `adapters/hop_links.py`, `config/fragments/hop.env`, `incident.env`, `stack-rf3.env` | Один конфиг |
| **Incident:** если `hop` ∈ `ENABLED_MODULES` — **не слать hop-алерты**, только `hop_links` в JSONL | `incident_sampler.py` | Нет дублей Telegram на RF3 |
| **Профиль RF3:** только `HOP_LINKS`; default `core,hop,incident` документирован | `stack-rf3.env`, `install/profiles.md` | Минимальный стек |
| **Тесты:** hop enabled → incident не эскалирует по hop; hop disabled → incident может | `tests/test_incident_hop_dedup.py` (новый) | Регресс на главный баг RF3 |

### Scope

- **В scope:** hop_links adapter, incident_sampler level logic, stack-rf3, docs, tests.
- **Вне scope:** перенос incident в `modules/incident/`, post-install RF3 probe.

### Критерии готовности

- [x] `stack-rf3` содержит только `HOP_LINKS` (без дублирующего `INCIDENT_HOP_LINKS` в профиле).
- [x] При `ENABLED_MODULES=core,hop,incident` hop-алерты идут только из `hop` tick.
- [x] JSONL incident по-прежнему содержит `hop_links` для post-mortem.
- [ ] `pytest`, `ruff` OK (на хосте с dev deps).

### Оценка объёма

~6–9 файлов, 1 агент.

---

## Фаза 10 — Incident как полноценный модуль (`modules/incident/`)

### Цели фазы

Сейчас `modules/incident/` — только `register.py`, а ~900 строк логики в `services/incident_sampler.py`. Это нарушает симметрию с `hop`, `mtproxy`, `vless` и усложняет понимание границ модуля.

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **Перенос** `incident_sampler.py` → `modules/incident/sampler.py` | новый пакет | Owner модуля = owner кода |
| **Выделение** `probes.py`, `postmortem.py`, `level.py` | `modules/incident/` | Тестируемость |
| **Удалить** `services/incident_sampler.py` и `bin/incident-sampler.sh`; entrypoint только `run incident` | `run_cli.py`, `systemd/cock-monitor-incident.*` | Без дублирующих путей |
| **Обновить** `docs/stage-5-unified-boundaries.md` | docs | Актуальная карта ownership |

### Scope

- **В scope:** move + split incident, run_cli, bin wrapper, tests import paths, stage-5 doc.
- **Вне scope:** новые фичи incident, изменение JSONL-формата.

### Критерии готовности

- [ ] `python -m cock_monitor run incident` вызывает код из `modules/incident/`.
- [ ] Нет `bin/incident-sampler.sh`, `cock-monitor-incident-sampler.*` в repo.
- [ ] Нет циклических импортов; `domain`/`adapters` не тянут telegram напрямую из sampler.
- [ ] Существующие тесты incident проходят (пути обновлены).
- [ ] `pytest`, `ruff` OK.

### Оценка объёма

~10–15 файлов (в основном move/rename), 1 агент.

---

## Фаза 11 — Post-install hooks и preflight по ролям

### Цели фазы

RF3 требует `setup-hop-probe.sh`, RF2 — `patch-xray-hop-http-proxy.sh`, Helsinki — `install/mtproto/*` вне модульной модели. Оператор должен видеть **что install сделал и что осталось**, а не помнить README наизусть.

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **Ключи профиля:** `POST_INSTALL_SCRIPTS`, `PREFLIGHT_SYSTEMD_UNITS`, `PREFLIGHT_TCP_PORTS` | `config/profiles/stack-rf3.env`, `stack-rf2-wg.env`, `stack-mtproxy.env` | Декларативные ops-требования роли |
| **Парсинг в** `platform/config.py` | `build_env_from_profile` не меняет семантику env; отдельный `load_profile_ops(profile)` | Разделение runtime env и ops metadata |
| **install_cli:** после enable timers — print checklist + опционально `--run-post-install` | `cock_monitor/install_cli.py` | Автоматизация без скрытых side effects по умолчанию |
| **preflight:** проверка `PREFLIGHT_*` для выбранного профиля | `cock_monitor/preflight.py` | `preflight --profile stack-rf3` до/после деплоя |
| **Документация** RF3/RF2/Helsinki runbooks | `install/profiles.md`, `install/rf3/README.md` (новый, краткий) | Снижение операционного риска |

### Scope

- **В scope:** profile keys, install_cli checklist, preflight extensions, docs.
- **Вне scope:** выполнение mtproto install внутри cock-monitor (остаётся отдельным скриптом).

### Критерии готовности

- [ ] `install --profile stack-rf3` выводит: «run install/rf3/setup-hop-probe.sh».
- [ ] `preflight --profile stack-rf3` проверяет наличие `xray-hop-probe.service` (если в PREFLIGHT).
- [ ] Post-install **не запускается** без явного флага (безопасность).
- [ ] `pytest`, `ruff` OK.

### Оценка объёма

~8–12 файлов, 1 агент.

---

## Фаза 12 — Роли (role presets) и lean-профили

### Цели фазы

Сейчас оператор выбирает сырой список модулей. Ввести **именованные роли** — пресеты над `ENABLED_MODULES` + валидация «для hop-gateway incident опционален, hop обязателен». Для Helsinki — **lean core** (минимум алертов при `stack-mtproxy`).

### Что меняем структурно и зачем

| Изменение | Файлы (ориентир) | Зачем |
|-----------|------------------|-------|
| **`platform/roles.py`:** `ROLE_PRESETS = {"hop-gateway": [...], "exit-node": [...], "mtproxy-only": [...]}` | новый модуль | Документированная матрица ролей в коде |
| **`install --role hop-gateway`** как sugar над `--profile` или замена | `install_cli.py`, `install/install.sh` | Проще деплой для агента/человека |
| **Валидация профиля:** `validate_profile_env(env) -> list[str]` warnings | `platform/config.py` | «exit-node без vless», «hop-gateway без HOP_LINKS» |
| **Lean core flags** в `stack-mtproxy.env`: отключить LA/MEM chart alerts, оставить минимум для telegram | `config/fragments/core.env`, `config/profiles/stack-mtproxy.env`, `modules/core/service.py` | Helsinki без лишнего шума |
| **Новые/уточнённые профили** при необходимости | `config/profiles/` | Явное соответствие бизнес-ролям |

### Scope

- **В scope:** roles.py, validation, lean core flags, install sugar, profiles, tests.
- **Вне scope:** удаление legacy shims (фаза 13).

### Критерии готовности

- [ ] `python -m cock_monitor config-check --profile stack-mtproxy` без warnings на lean-конфиге.
- [ ] `validate_profile_env` ловит `stack-rf3` без `HOP_LINKS`.
- [ ] `install --role mtproxy-only` эквивалентен `stack-mtproxy`.
- [ ] Документирована таблица role → modules → VPS.
- [ ] `pytest`, `ruff` OK.

### Оценка объёма

~10–14 файлов, 1 агент.

---

## Фаза 13 — Legacy cleanup (безжалостная чистка)

### Цели фазы

Убрать весь v1-мусор: shim-пакеты, дублирующие entrypoints, legacy systemd, старые install-скрипты и docs. После фазы в репо **один** путь для каждой операции.

### Инвентарь на удаление / миграцию

| Категория | Артефакты | Действие |
|-----------|-----------|----------|
| **Shim-пакеты** | `mtproxy_module/`, `telegram_bot/` (re-export only) | Удалить; импорты → `cock_monitor.*` |
| **Legacy systemd** | `cock-monitor.service`, `cock-monitor-incident-sampler.*`, `cock-shaper.*`, `cock-mtproxy-monitor.*` (v1 names в docs) | Удалить из repo; оставить в `LEGACY_UNITS` uninstall |
| **Legacy bin** | `bin/incident-sampler.sh` | Удалить |
| **configure_cli** | v1 `BASE_SERVICES`, `cock-mtproxy-*` units | Переписать на v2 units или удалить wizard в пользу `install.sh` |
| **lib/** | `lib/incident-metrics.sh` | Удалить если не используется |
| **install/** | `install-ubuntu-minimal.sh` (если дублирует v2), legacy unit refs в DEPLOY | Синхронизировать или удалить |
| **Docs** | `stage-0-inventory` v1 units, `docs/tasks-vpn-quality-investigation.md` `SHAPER_ENABLE` | Обновить на v2 only |
| **Burst-capture** | CLI без registry | Зарегистрировать `diagnostics` или subcommand в `__main__.py` |
| **JSONL tag** | `"sampler": "incident-sampler"` | Опционально переименовать в `"incident"` (breaking JSONL — ok) |

### Scope

- **В scope:** всё из таблицы, grep по `MTPROXY_ENABLE|INCIDENT_SAMPLER|SHAPER_ENABLE|INCIDENT_HOP_LINKS|incident-sampler`, doc sync.
- **Вне scope:** новые фичи модулей.

### Критерии готовности

- [ ] `rg` по legacy-ключам в `*.py`/`*.sh`/`config*` — пусто (кроме `LEGACY_UNITS`, uninstall, migration doc «removed»).
- [ ] Нет рабочих импортов из `mtproxy_module` / `telegram_bot`.
- [ ] `python -m cock_monitor burst-capture --help` работает (или модуль в registry).
- [ ] README/DEPLOY/install — только v2 timers и `ENABLED_MODULES`.
- [ ] `pytest`, `ruff` OK.

### Оценка объёма

~15–25 файлов (много delete + docs), 1 агент. **Частично начато в фазах 7–9** (удалены fallback-флаги, incident v2 units).

---

## Сводная дорожная карта

```text
Фаза 7  → единый enable (удалить legacy env-флаги)
Фаза 8  → install daily timers + матрица профилей
Фаза 9  → HOP_LINKS only + dedup алертов hop/incident
Фаза 10 → incident в modules/incident/ (без wrapper)
Фаза 11 → post-install hooks + preflight по ролям
Фаза 12 → role presets + lean mtproxy + validation
Фаза 13 → legacy cleanup (shims, units, bin, docs)
```

| Фаза | Зависимости | Приоритет для бизнеса |
|------|-------------|----------------------|
| 7 | — | **Высокий** (DE/US shaper) |
| 8 | — | **Высокий** (daily reports, Helsinki) |
| 9 | 7 | **Высокий** (RF3 дубли алертов) |
| 13 | 7–9 частично | **Средний** (гигиена; инкрементально после 7–9) |
| 10 | 9 | Средний (структура кода) |
| 11 | 8 | Средний (ops RF3/RF2/Helsinki) |
| 12 | 7, 8, 9 | Средний (ergonomics деплоя) |

---

## Целевое состояние (после фаз 7–13)

```text
Profile (role + host overrides)
    ↓
ENABLED_MODULES  ← единственный switch модулей
    ↓
install_cli → modular timers + daily timers + checklist
    ↓
run <module> / telegram dispatch
    ↓
metrics.db (per-module tables, один файл на VPS)

Hop alerts: только modules/hop
Incident: JSONL + post-mortem, без hop-alerts если hop включён
Shaper: активен iff shaper ∈ ENABLED_MODULES
Helsinki: stack-mtproxy (lean core + mtproxy)
```

---

## Связанные документы

- [`docs/refactoring-plan.md`](refactoring-plan.md) — этапы 0–6 (выполнены)
- [`docs/refactoring-stage-reports.md`](refactoring-stage-reports.md) — отчёты агентов
- [`docs/v2-migration.md`](v2-migration.md) — breaking v2 upgrade
- [`install/profiles.md`](../install/profiles.md) — матрица VPS
- [`docs/stage-5-unified-boundaries.md`](stage-5-unified-boundaries.md) — границы VLESS/Incident/Shaper

---

## Шаблон отчёта (фазы 7+)

Добавлять в `docs/refactoring-stage-reports.md`:

```markdown
## Отчёт по фазе N

- Цель фазы: [...]
- Структурные изменения: [...]
- Зачем: [...]
- Изменённые файлы: [...]
- Breaking changes: [нет / да]
- Обновления документации: [...]
- Регресс: pytest [ok/fail], ruff [ok/fail], smoke [кратко]
- Критерии готовности: [выполнены/нет]
- Готовность к следующей фазе: [да/нет]
```
