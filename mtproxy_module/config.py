from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cock_monitor.defaults import DEFAULT_METRICS_DB


def to_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def to_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MtproxyConfig:
    enabled: bool
    db_path: Path
    mtproxy_port: int
    max_connections_per_ip: int
    max_unique_ips: int
    alert_cooldown_minutes: int
    daily_report_top_n: int
    conntrack_enabled: bool
    conntrack_warn_fill_percent: int
    conntrack_crit_fill_percent: int

    @classmethod
    def from_env_map(cls, env: dict[str, str]) -> MtproxyConfig:
        db = env.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
        return cls(
            enabled=to_bool(env.get("MTPROXY_ENABLE"), False),
            db_path=Path(db).expanduser(),
            mtproxy_port=to_int(env.get("MTPROXY_PORT"), 8443),
            max_connections_per_ip=to_int(env.get("MTPROXY_MAX_CONNECTIONS_PER_IP"), 20),
            max_unique_ips=to_int(env.get("MTPROXY_MAX_UNIQUE_IPS"), 50),
            alert_cooldown_minutes=to_int(env.get("MTPROXY_ALERT_COOLDOWN_MINUTES"), 30),
            daily_report_top_n=to_int(env.get("MTPROXY_DAILY_TOP_N"), 10),
            conntrack_enabled=to_bool(env.get("MTPROXY_CONNTRACK_ENABLE"), False),
            conntrack_warn_fill_percent=to_int(env.get("MTPROXY_CONNTRACK_WARN_FILL_PERCENT"), 80),
            conntrack_crit_fill_percent=to_int(env.get("MTPROXY_CONNTRACK_CRIT_FILL_PERCENT"), 95),
        )
