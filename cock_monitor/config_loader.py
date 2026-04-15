"""Load, validate and expose typed application configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cock_monitor.config_schema import AppConfig
from cock_monitor.env import parse_env_file


@dataclass(frozen=True)
class ConfigValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class LoadedConfig:
    env_path: Path
    app: AppConfig
    validation: ConfigValidationResult


def _validate_percent(name: str, value: int, errors: list[str]) -> None:
    if value < 1 or value > 100:
        errors.append(f"{name} must be within 1..100")


def validate_config(cfg: AppConfig) -> ConfigValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    _validate_percent("WARN_PERCENT", cfg.conntrack.warn_percent, errors)
    _validate_percent("CRIT_PERCENT", cfg.conntrack.crit_percent, errors)
    if cfg.conntrack.warn_percent >= cfg.conntrack.crit_percent:
        errors.append("WARN_PERCENT must be lower than CRIT_PERCENT")

    if cfg.load_alert.warn_threshold <= 0:
        errors.append("LA_WARN_THRESHOLD must be > 0")
    if cfg.metrics.stats_delta_min_interval_sec < 0:
        errors.append("STATS_DELTA_MIN_INTERVAL_SEC must be >= 0")
    if cfg.mtproxy.enabled:
        if not cfg.telegram.bot_token or not cfg.telegram.chat_id:
            errors.append("MTPROXY_ENABLE=1 requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    if cfg.incident.alert_enable:
        if not cfg.telegram.bot_token or not cfg.telegram.chat_id:
            errors.append(
                "INCIDENT_ALERT_ENABLE=1 requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

    if cfg.vless.xui_db_path and not Path(cfg.vless.xui_db_path).expanduser().is_file():
        warnings.append("XUI_DB_PATH is set but the file is not accessible on this host")

    known_prefixes = {
        "TELEGRAM_",
        "MAX_",
        "WARN_",
        "CRIT_",
        "COOLDOWN_",
        "STATE_",
        "CHECK_",
        "INCLUDE_",
        "DRY_",
        "ALERT_",
        "STATS_",
        "METRICS_",
        "MTPROXY_",
        "XUI_",
        "VLESS_",
        "INCIDENT_",
        "SHAPER_",
        "STATUS_",
        "LA_",
    }
    for key in cfg.raw:
        if any(key.startswith(p) for p in known_prefixes):
            continue
        warnings.append(f"Unknown config key: {key}")

    return ConfigValidationResult(errors=errors, warnings=warnings)


def load_config(env_path: Path) -> LoadedConfig:
    path = env_path.expanduser().resolve()
    env = parse_env_file(path)
    app = AppConfig.from_env_map(env)
    return LoadedConfig(env_path=path, app=app, validation=validate_config(app))
