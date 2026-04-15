"""Typed config schema and validation for .env runtime format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cock_monitor.defaults import DEFAULT_METRICS_DB, DEFAULT_STATE_FILE


def _as_int(raw: str | None, default: int) -> int:
    s = (raw or "").strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _as_float(raw: str | None, default: float) -> float:
    s = (raw or "").strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _as_bool(raw: str | None, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    offset_file: str
    max_updates_per_run: int
    max_seconds_per_run: int


@dataclass(frozen=True)
class ConntrackConfig:
    warn_percent: int
    crit_percent: int
    cooldown_seconds: int
    state_file: Path
    check_conntrack_fill: bool
    include_conntrack_stats_line: bool
    dry_run: bool


@dataclass(frozen=True)
class MetricsConfig:
    db_path: Path
    record_every_run: bool
    record_min_interval_sec: int
    retention_days: int
    max_rows: int
    alert_on_stats: bool
    stats_drop_min: int
    stats_insert_failed_min: int
    stats_cooldown_seconds: int
    alert_on_stats_delta: bool
    stats_delta_min_interval_sec: int


@dataclass(frozen=True)
class MtproxyConfigModel:
    enabled: bool
    port: int
    alert_cooldown_minutes: int
    max_connections_per_ip: int
    max_unique_ips: int
    daily_top_n: int


@dataclass(frozen=True)
class VlessConfig:
    xui_db_path: str
    daily_tz: str
    telegram_display_tz: str
    daily_top_n: int
    abuse_gb: float
    abuse_share_pct: float
    daily_min_total_mb: int
    ip_top_k: int
    ip_parse_max_mb: int


@dataclass(frozen=True)
class IncidentConfig:
    enabled: bool
    alert_enable: bool
    alert_cooldown_sec: int
    log_dir: Path
    state_file: Path


@dataclass(frozen=True)
class ShaperConfig:
    enabled: bool
    iface: str
    status_file: Path
    max_rate_mbit: int
    min_rate_mbit: int
    cpu_target_pct: int


@dataclass(frozen=True)
class LoadAlertConfig:
    enabled: bool
    warn_threshold: float
    cooldown_sec: int


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    conntrack: ConntrackConfig
    metrics: MetricsConfig
    mtproxy: MtproxyConfigModel
    vless: VlessConfig
    incident: IncidentConfig
    shaper: ShaperConfig
    load_alert: LoadAlertConfig
    raw: dict[str, str]

    @classmethod
    def from_env_map(cls, env: dict[str, str]) -> AppConfig:
        cooldown = _as_int(env.get("COOLDOWN_SECONDS"), 3600)
        state_file = Path(env.get("STATE_FILE", DEFAULT_STATE_FILE))
        telegram_offset = env.get("TELEGRAM_OFFSET_FILE", "").strip()
        if not telegram_offset:
            telegram_offset = str(state_file.parent / "telegram_offset")
        return cls(
            telegram=TelegramConfig(
                bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
                chat_id=env.get("TELEGRAM_CHAT_ID", "").strip(),
                offset_file=telegram_offset,
                max_updates_per_run=max(1, _as_int(env.get("MAX_UPDATES_PER_RUN"), 200)),
                max_seconds_per_run=max(1, _as_int(env.get("MAX_SECONDS_PER_RUN"), 20)),
            ),
            conntrack=ConntrackConfig(
                warn_percent=_as_int(env.get("WARN_PERCENT"), 80),
                crit_percent=_as_int(env.get("CRIT_PERCENT"), 95),
                cooldown_seconds=max(0, cooldown),
                state_file=state_file,
                check_conntrack_fill=_as_bool(env.get("CHECK_CONNTRACK_FILL"), default=True),
                include_conntrack_stats_line=_as_bool(
                    env.get("INCLUDE_CONNTRACK_STATS_LINE"), default=True
                ),
                dry_run=_as_bool(env.get("DRY_RUN"), default=False),
            ),
            metrics=MetricsConfig(
                db_path=Path(env.get("METRICS_DB", DEFAULT_METRICS_DB)),
                record_every_run=_as_bool(env.get("METRICS_RECORD_EVERY_RUN"), default=True),
                record_min_interval_sec=max(
                    0, _as_int(env.get("METRICS_RECORD_MIN_INTERVAL_SEC"), 0)
                ),
                retention_days=max(0, _as_int(env.get("METRICS_RETENTION_DAYS"), 14)),
                max_rows=max(0, _as_int(env.get("METRICS_MAX_ROWS"), 0)),
                alert_on_stats=_as_bool(env.get("ALERT_ON_STATS"), default=False),
                stats_drop_min=max(0, _as_int(env.get("STATS_DROP_MIN"), 0)),
                stats_insert_failed_min=max(0, _as_int(env.get("STATS_INSERT_FAILED_MIN"), 0)),
                stats_cooldown_seconds=max(
                    0, _as_int(env.get("STATS_COOLDOWN_SECONDS"), cooldown)
                ),
                alert_on_stats_delta=_as_bool(env.get("ALERT_ON_STATS_DELTA"), default=False),
                stats_delta_min_interval_sec=max(
                    0, _as_int(env.get("STATS_DELTA_MIN_INTERVAL_SEC"), 60)
                ),
            ),
            mtproxy=MtproxyConfigModel(
                enabled=_as_bool(env.get("MTPROXY_ENABLE"), default=False),
                port=_as_int(env.get("MTPROXY_PORT"), 8443),
                alert_cooldown_minutes=max(
                    0, _as_int(env.get("MTPROXY_ALERT_COOLDOWN_MINUTES"), 30)
                ),
                max_connections_per_ip=max(
                    1, _as_int(env.get("MTPROXY_MAX_CONNECTIONS_PER_IP"), 20)
                ),
                max_unique_ips=max(1, _as_int(env.get("MTPROXY_MAX_UNIQUE_IPS"), 50)),
                daily_top_n=max(1, _as_int(env.get("MTPROXY_DAILY_TOP_N"), 10)),
            ),
            vless=VlessConfig(
                xui_db_path=env.get("XUI_DB_PATH", "").strip(),
                daily_tz=env.get("VLESS_DAILY_TZ", "Europe/Moscow").strip() or "Europe/Moscow",
                telegram_display_tz=(
                    env.get("VLESS_TELEGRAM_DISPLAY_TZ", "Europe/Moscow").strip()
                    or "Europe/Moscow"
                ),
                daily_top_n=max(1, _as_int(env.get("VLESS_DAILY_TOP_N"), 10)),
                abuse_gb=max(0.0, _as_float(env.get("VLESS_ABUSE_GB"), 20.0)),
                abuse_share_pct=max(0.0, _as_float(env.get("VLESS_ABUSE_SHARE_PCT"), 40.0)),
                daily_min_total_mb=max(0, _as_int(env.get("VLESS_DAILY_MIN_TOTAL_MB"), 500)),
                ip_top_k=max(1, _as_int(env.get("VLESS_IP_TOP_K"), 3)),
                ip_parse_max_mb=max(1, _as_int(env.get("VLESS_IP_PARSE_MAX_MB"), 256)),
            ),
            incident=IncidentConfig(
                enabled=_as_bool(env.get("INCIDENT_SAMPLER_ENABLE"), default=False),
                alert_enable=_as_bool(env.get("INCIDENT_ALERT_ENABLE"), default=False),
                alert_cooldown_sec=max(0, _as_int(env.get("INCIDENT_ALERT_COOLDOWN_SEC"), 300)),
                log_dir=Path(env.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor")),
                state_file=Path(
                    env.get(
                        "INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state"
                    )
                ),
            ),
            shaper=ShaperConfig(
                enabled=_as_bool(env.get("SHAPER_ENABLE"), default=False),
                iface=env.get("SHAPER_IFACE", "ens3").strip() or "ens3",
                status_file=Path(
                    env.get("SHAPER_STATUS_FILE", "/var/lib/cock-monitor/cpu_shaper.status")
                ),
                max_rate_mbit=max(1, _as_int(env.get("SHAPER_MAX_RATE_MBIT"), 100)),
                min_rate_mbit=max(1, _as_int(env.get("SHAPER_MIN_RATE_MBIT"), 10)),
                cpu_target_pct=max(1, min(100, _as_int(env.get("SHAPER_CPU_TARGET_PCT"), 70))),
            ),
            load_alert=LoadAlertConfig(
                enabled=_as_bool(env.get("LA_ALERT_ENABLE"), default=False),
                warn_threshold=_as_float(env.get("LA_WARN_THRESHOLD"), 1.5),
                cooldown_sec=max(0, _as_int(env.get("LA_ALERT_COOLDOWN_SEC"), 600)),
            ),
            raw=dict(env),
        )
