# install/

Краткая документация по one-shot инсталлятору `cock-monitor`.

## Что это

`install/install-ubuntu-minimal.sh` — интерактивная установка минимальной рабочей конфигурации из **текущего клона** репозитория.

Запуск:

```bash
sudo bash install/install-ubuntu-minimal.sh
```

Скрипт:

- спрашивает `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`;
- создает/обновляет `.venv` в репозитории;
- ставит базовые пакеты через `apt`;
- создает `/etc/cock-monitor.env` и `/var/lib/cock-monitor`;
- устанавливает unit/timer файлы и включает:
  - `cock-monitor.timer`
  - `cock-monitor-telegram-bot.timer`
  - `cock-monitor-daily.timer`

## Ограничения

- Поддерживаемый целевой сценарий: Ubuntu + systemd + запуск от root (`sudo`).
- Это **минимальная** конфигурация (без MTProxy, incident sampler, shaper).
- Сервисы запускаются из текущего пути репозитория через systemd override и `.venv/bin/python`.
  - Если переместить или удалить клон после установки, сервисы перестанут корректно стартовать.
- Инсталлятор не предназначен для non-interactive/CI режима.

## Повторный запуск (идемпотентность)

Повторный запуск допустим и безопасен:

- зависимости и `.venv` будут доведены до актуального состояния;
- unit/timer и override будут переустановлены;
- при существующем `/etc/cock-monitor.env` скрипт спросит подтверждение на перезапись.

Рекомендация:

- если боевой `/etc/cock-monitor.env` уже настроен вручную, при повторном запуске отвечайте `N` на перезапись env-файла.

## Безопасная переустановка systemd override

Если нужно переустановить только unit/timer и override (например, после изменения пути клона):

1. Остановите таймеры:

```bash
sudo systemctl disable --now \
  cock-monitor.timer \
  cock-monitor-telegram-bot.timer \
  cock-monitor-daily.timer
```

2. Удалите старые override (если были):

```bash
sudo rm -rf \
  /etc/systemd/system/cock-monitor.service.d \
  /etc/systemd/system/cock-monitor-telegram-bot.service.d \
  /etc/systemd/system/cock-monitor-daily.service.d
```

3. Запустите инсталлятор из нового/актуального пути репозитория:

```bash
cd /path/to/cock-monitor
sudo bash install/install-ubuntu-minimal.sh
```

4. Проверьте итог:

```bash
systemctl list-timers --all | awk 'NR==1 || /cock-monitor/'
sudo systemctl status cock-monitor.service --no-pager
sudo systemctl status cock-monitor-telegram-bot.service --no-pager
sudo systemctl status cock-monitor-daily.service --no-pager
```

## Быстрая диагностика

```bash
journalctl -u cock-monitor.service -n 100 --no-pager
journalctl -u cock-monitor-telegram-bot.service -n 100 --no-pager
journalctl -u cock-monitor-daily.service -n 100 --no-pager
```
