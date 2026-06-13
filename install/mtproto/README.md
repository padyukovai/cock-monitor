# MTProxy on VPS

## Восстановление после переустановки ОС

Скрипт [`restore-mtproxy.sh`](restore-mtproxy.sh) + секреты в [`restore-data/`](restore-data/) (в `.gitignore`).

```bash
git clone <repo> /opt/cock-monitor
PUBLIC_IP=163.5.153.32 bash /opt/cock-monitor/install/mtproto/restore-mtproxy.sh
```

Секреты те же — **IP в ссылках меняется** (`server=...`). `proxy-secret` / `proxy-multi.conf` скачиваются с core.telegram.org; обновление — systemd timer `mtproto-config-refresh.timer` (hourly).

После restore опционально: `stabilize-vps.sh` (только при reconnect-storm), `enable-incident-sampler.sh`.

---

## FakeTLS (дополнительные ключи)

См. `add-faketls-secrets.sh` — добавляет **FakeTLS (`ee`)** секреты к plain (`dd`) MTProxy на порту **8443**.

## Стабилизация VPS (`stabilize-vps.sh`)

Скрипт для London и аналогичных MTProxy-хостов под reconnect-storm:

1. **iptables connlimit** — **отключён по умолчанию** (`CONNLIMIT=0`); лимит 35 ломал Telegram из‑за TIME-WAIT в conntrack. Включить: `CONNLIMIT=35 bash stabilize-vps.sh`
2. **mtproto-run.sh** — `-c 3000`, `--max-accept-rate 50`
3. **conntrack** — autoload + `nf_conntrack_max=65536`
4. **swap** — 1 GB `/swapfile`, `vm.swappiness=10`
5. **sshd drop-in** — `UseDNS no`, `GSSAPIAuthentication no`, `MaxStartups 30:50:200`
6. **mtproto CPUQuota** — снят (55% → без лимита)
7. **tcp_max_syn_backlog** — 4096
8. **metrics** — systemd override на `.venv/bin/python` для `cock-mtproxy-monitor` / `cock-mtproxy-daily`

```bash
sudo bash install/mtproto/stabilize-vps.sh
```

Переменные: `CONNLIMIT=35`, `SWAP_SIZE=1G`, `COCK_MONITOR_HOME=/opt/cock-monitor`.

### Rollback

- `iptables -D INPUT -p tcp --dport 8443 -m connlimit --connlimit-above 35 --connlimit-mask 32 -j REJECT`
- восстановить `mtproto-run.sh.bak.*`, `resource-limits.conf.bak.*`
- `swapoff /swapfile`, убрать из `/etc/fstab`
- удалить `/etc/ssh/sshd_config.d/99-cock-monitor-hardening.conf` + `systemctl reload ssh`

---

## FakeTLS: установка


1. Генерирует 5 новых секретов в `/etc/mtproto/server-secrets-faketls.txt`
2. Записывает домен маскировки в `/etc/mtproto/faketls-domain.conf` (по умолчанию `www.google.com`)
3. Обновляет `/usr/local/bin/mtproto-run.sh` — один процесс обслуживает **и dd, и ee**
4. Перезапускает `mtproto.service`
5. Печатает клиентские ссылки и сохраняет их в `/etc/mtproto/client-links-faketls.txt`

Старые plain-ссылки (`dd…`) **не меняются**.

## Установка на VPS

```bash
cd /opt/cock-monitor   # или скопировать install/mtproto/
sudo FAKETLS_DOMAIN=www.google.com bash install/mtproto/add-faketls-secrets.sh
```

Переменные окружения:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `FAKETLS_DOMAIN` | `www.google.com` | Домен для `-D` (TLS camouflage) |
| `MTPROXY_PUBLIC_IP` | `163.5.41.47` | IP в ссылках |
| `MTPROXY_PORT` | `8443` | Порт в ссылках |
| `FAKETLS_COUNT` | `5` | Число ключей |

## Показать ссылки снова

```bash
sudo /usr/local/bin/mtproto-show-faketls-links.sh
```

## Ограничения

- FakeTLS (`ee`) может **не работать** на Telegram Desktop 6.3+ и свежих мобильных клиентах — для них оставляйте старые `dd`-ссылки.
- Для пользователей из РФ: сначала тестируйте `ee`, при проблемах — `dd`.
