# Burst-диагностика London vs USA

Протокол VPS-side сбора метрик при параллельном шквале VLESS+Vision подключений. Клиентские тесты (`simulate`, `path-check`) запускаются с Mac из проекта **cockvpn**; серверный сбор — только **cock-monitor** на VPS.

## Симптом (контекст)

- **London** (`cock-london`, `163.5.41.47`): одиночные `probe` / `path-check` — ok; `simulate parallel ×8` — 0/8.
- **USA** (`cock-is`): с того же Mac parallel 8/8 ok.
- На London при burst: растут TCP `:443 established` и `orphaned`, в `/var/log/x-ui/3xipl-ap.log` нет новых `accepted`.

## Prerequisites

1. cock-monitor установлен в `/opt/cock-monitor`, env в `/etc/cock-monitor.env`.
2. На VPS: `cd /opt/cock-monitor && git pull && .venv/bin/pip install -e ".[dev]"`
3. Preflight: `.venv/bin/python -m cock_monitor preflight /etc/cock-monitor.env`

## Фаза A — включить incident sampler (фон 10s)

На **London** (и позже на **USA** для сравнения) добавьте в `/etc/cock-monitor.env`:

```bash
INCIDENT_SAMPLER_ENABLE=1
INCIDENT_TCP_PROBE_PORTS=443
INCIDENT_TCP_PROBE_LOCAL_TARGET=127.0.0.1
INCIDENT_TCP_PROBE_EXTERNAL_TARGET=163.5.41.47   # public IP этого VPS
INCIDENT_ALERT_ENABLE=0
```

Включите timer:

```bash
sudo systemctl enable --now cock-monitor-incident-sampler.timer
sudo systemctl status cock-monitor-incident-sampler.timer
```

Проверка одного тика:

```bash
sudo INCIDENT_SAMPLER_ENABLE=1 .venv/bin/python -m cock_monitor.services.incident_sampler /etc/cock-monitor.env
tail -1 /var/lib/cock-monitor/incident-$(date -u +%Y%m%d).jsonl
```

Для **USA** замените `INCIDENT_TCP_PROBE_EXTERNAL_TARGET` на public IP USA-хоста.

Интерактивная настройка (TCP probe в wizard): `sudo .venv/bin/python -m cock_monitor configure`.

## Фаза B/C — burst capture (1 Hz, on-demand)

Добавьте в `/etc/cock-monitor.env` (см. также `config.example.env`):

```bash
BURST_CAPTURE_LOG_DIR=/var/lib/cock-monitor
BURST_ACCESS_LOG_PATH=/var/log/x-ui/3xipl-ap.log
BURST_ERROR_LOG_PATH=/var/log/x-ui/error.log
BURST_PROBE_PORT=443
# Опционально: IP вашего Mac для delta_from_ip
# BURST_CLIENT_IP=<your-mac-public-ip>
```

### Протокол теста London vs USA

**На VPS (SSH):**

```bash
cd /opt/cock-monitor
sudo .venv/bin/python -m cock_monitor burst-capture --env-file /etc/cock-monitor.env start --duration 60
```

**На Mac (cockvpn):**

```bash
# baseline
.venv/bin/python -m vless_test_stand path-check london
# burst
.venv/bin/python -m vless_test_stand simulate london --parallel 8
```

**Снова на VPS:**

```bash
sudo .venv/bin/python -m cock_monitor burst-capture --env-file /etc/cock-monitor.env stop
# путь из вывода stop/status
sudo .venv/bin/python -m cock_monitor burst-capture report /var/lib/cock-monitor/burst-YYYYMMDD-HHMMSS.jsonl
# JSON:
sudo .venv/bin/python -m cock_monitor burst-capture report /var/lib/cock-monitor/burst-....jsonl --json
# если клиент fail:
sudo .venv/bin/python -m cock_monitor burst-capture report ... --client-failed
```

Повторите тот же сценарий на **USA** (`ssh cock-is`).

### Diff (ожидаемое)

| Метрика | London (проблема) | USA (контроль) |
|---------|-------------------|----------------|
| `port443.estab` peak | растёт (≥3) | растёт |
| `access_log.delta_accepted` sum | **0** | **>0** |
| `ss.orphan` peak | часто ↑ | ниже |
| Verdict | `handshake_stall` | `ok` |

## Вердикты burst-report

| Verdict | Интерпретация |
|---------|---------------|
| `handshake_stall` | TCP на :443 есть, `accepted` в access log не растёт — VLESS/Reality handshake не завершается |
| `conntrack_pressure` | `fill_pct` ≥85% или высокий `syn_recv` на :443 |
| `syn_backlog` | `ListenOverflows` > 0 — упирается в `tcp_max_syn_backlog` |
| `post_auth_failure` | `accepted` есть, клиент fail — проблема после auth (egress) |
| `xray_saturated` | много FD при низком CPU — копать конфиг xray/Vision |
| `ok` | `accepted` растёт, burst обработан |

## Troubleshooting

| Проблема | Решение |
|----------|---------|
| `cock-monitor-incident-sampler.timer` inactive | `sudo systemctl enable --now cock-monitor-incident-sampler.timer` |
| Пустой `incident-*.jsonl` | `INCIDENT_SAMPLER_ENABLE=1` в env |
| `burst-capture: already running` | `burst-capture stop` или удалить stale `/var/lib/cock-monitor/burst-capture.state` |
| `xray.pid=0` | проверить `pgrep -a xray`, `BURST_XRAY_PROCESS_MATCH` |
| `delta_accepted` всегда 0, путь лога неверный | `ls -la /var/log/x-ui/3xipl-ap.log`, сверить с 3x-ui bind mount |
| Нет `ss` / `pgrep` | `apt install iproute2 procps` |

## См. также

- [README.md — Incident sampler](../README.md#incident-sampler-короткие-постмортем-срезы)
- [config.example.env](../config.example.env) — блоки `INCIDENT_*` и `BURST_*`
