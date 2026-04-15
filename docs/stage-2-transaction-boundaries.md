# Этап 2: transaction boundaries (MTProxy storage)

Этот документ фиксирует логические транзакции для MTProxy-сценариев после изменений Этапа 2.

## Границы транзакций

1. `bin/cock-mtproxy-collect.py` (основной сбор + алерты):
   - одна транзакция `scenario_transaction(...)` на сценарий;
   - в неё входят:
     - обновление `mtproxy_state` (`prev_bytes_*`);
     - запись выборки в `mtproxy_metrics`;
     - запись отправленных алертов в `mtproxy_alerts`.
   - при ошибке внутри сценария выполняется rollback всей транзакции.

2. Точечные mutate-команды (например `/mt_threshold`):
   - одиночная операция выполняется в собственной транзакции;
   - если уже есть внешняя транзакция, внутренний commit не делается.

3. Read/query-сценарии (`summary_rows`, `can_send_alert`, чтение state):
   - только чтение, без `commit`.

4. Schema/migrations:
   - `migrate_schema`/`init_schema` выполняют DDL в отдельной транзакции;
   - фиксируется `PRAGMA user_version=1` для схемы MTProxy.

## Инварианты

- Низкоуровневые mutate-функции (`_state_set`, `store_metric`, `record_alert`) не делают `commit` напрямую.
- `commit` происходит только на boundary сценария.
- Ошибка в середине сценария не оставляет partial-write.
