# Incident sampler (сетевое здоровье VPS)

JSONL-срезы каждые **10 секунд** без Telegram: ping, DNS, conntrack, TCP states, TCP-probe портов, systemd units.

## Включение на VPS

```bash
sudo PUBLIC_IP=163.5.41.47 bash /opt/cock-monitor/install/incident/enable-incident-sampler.sh
```

По умолчанию:
- `INCIDENT_ALERT_ENABLE=0`, `INCIDENT_POSTMORTEM_ENABLE=0` — только логи
- TCP-probe local `127.0.0.1` + external `PUBLIC_IP` на порты **22, 8443, 443**
- units: `mtproto.service`, `ssh.service`, `x-ui.service`

## Просмотр по SSH

```bash
incident-status              # последние 10 срезов
incident-status --last 50    # больше истории
incident-status --day 20260613

# сырой JSONL
tail -5 /var/lib/cock-monitor/incident-$(date -u +%Y%m%d).jsonl
grep '"level":"WARN"' /var/lib/cock-monitor/incident-*.jsonl | tail
```

## Файлы

| Путь | Назначение |
|------|------------|
| `/var/lib/cock-monitor/incident-YYYYMMDD.jsonl` | срезы за UTC-день |
| `/var/lib/cock-monitor/incident_sampler.state` | streak / incident window |

Когда появится Telegram-токен: `INCIDENT_ALERT_ENABLE=1`, `INCIDENT_POSTMORTEM_ENABLE=1` в `/etc/cock-monitor.env`.
