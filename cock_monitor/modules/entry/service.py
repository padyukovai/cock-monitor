"""Entry collect tick: access.log accepts, error.log TLS signals, alert."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cock_monitor.adapters.hop_links import collect_hop_links, resolve_hop_links_raw
from cock_monitor.adapters.linux_host import read_hostname_fqdn
from cock_monitor.adapters.xray_access_log import XrayAccessLogTracker
from cock_monitor.adapters.xray_error_log import XrayErrorLogTracker
from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.modules.entry.alerts import EntryAlertThresholds, evaluate_entry_alerts
from cock_monitor.modules.entry.storage import insert_sample, record_alert, should_alert
from cock_monitor.platform.registry import module_enabled
from cock_monitor.platform.storage.manager import StorageManager
from cock_monitor.platform.telegram.client import TelegramClient


def _as_bool(raw: str, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s not in {"0", "false", "False", "no", "NO"}


def _as_int(raw: str, default: int) -> int:
    s = (raw or "").strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _as_float(raw: str, default: float) -> float:
    s = (raw or "").strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _load_last_ts(state_path: Path) -> int:
    if not state_path.is_file():
        return 0
    for line in state_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("last_ts="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def _save_last_ts(state_path: Path, ts: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    text = f"last_ts={ts}\n"
    tmp = state_path.parent / f".entry-tick.{state_path.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(state_path)


def _parse_inbound_tags(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()]
    return tuple(parts)


def _hop_links_ok(hop_links_raw: str) -> bool:
    data = collect_hop_links(hop_links_raw)
    if not data.get("enabled"):
        return True
    for link in data.get("links", []):
        if str(link.get("error") or "").strip():
            return False
    return True


@dataclass(frozen=True)
class EntryConfig:
    access_log_path: Path
    error_log_path: Path
    access_state_path: Path
    error_state_path: Path
    tick_state_path: Path
    log_dir: Path
    inbound_tags: tuple[str, ...]
    primary_inbound: str
    secondary_inbound: str
    hop_links_raw: str
    min_interval_sec: int
    alert_enable: bool
    alert_cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    proxy_url: str | None
    metrics_db: Path
    thresholds: EntryAlertThresholds

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> EntryConfig:
        state_dir = Path(raw.get("ENTRY_STATE_DIR", "/var/lib/cock-monitor"))
        tags = _parse_inbound_tags(
            raw.get("ENTRY_INBOUND_TAGS", "in-443-tcp,in-8443-tcp")
        )
        primary = raw.get("ENTRY_PRIMARY_INBOUND", "in-443-tcp").strip() or "in-443-tcp"
        secondary = raw.get("ENTRY_SECONDARY_INBOUND", "in-8443-tcp").strip() or "in-8443-tcp"
        access_default = raw.get("VLESS_ACCESS_LOG_PATH", "/var/log/x-ui/access.log").strip()
        error_default = raw.get("HOP_ERROR_LOG_PATH", "/var/log/x-ui/error.log").strip()
        return cls(
            access_log_path=Path(raw.get("ENTRY_ACCESS_LOG_PATH", access_default)),
            error_log_path=Path(raw.get("ENTRY_ERROR_LOG_PATH", error_default)),
            access_state_path=state_dir / "entry_access_log.state",
            error_state_path=state_dir / "entry_error_log.state",
            tick_state_path=state_dir / "entry_tick.state",
            log_dir=Path(raw.get("ENTRY_LOG_DIR", "/var/lib/cock-monitor")),
            inbound_tags=tags,
            primary_inbound=primary,
            secondary_inbound=secondary,
            hop_links_raw=resolve_hop_links_raw(raw),
            min_interval_sec=max(10, _as_int(raw.get("ENTRY_MIN_INTERVAL_SEC", ""), 60)),
            alert_enable=_as_bool(raw.get("ENTRY_ALERT_ENABLE", ""), default=False),
            alert_cooldown_sec=_as_int(raw.get("ENTRY_ALERT_COOLDOWN_SEC", ""), 600),
            dry_run=dry_run or _as_bool(raw.get("DRY_RUN", ""), default=False),
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            proxy_url=raw.get("TELEGRAM_PROXY_URL", "").strip() or None,
            metrics_db=Path(raw.get("METRICS_DB", DEFAULT_METRICS_DB)),
            thresholds=EntryAlertThresholds(
                accept_primary_min_per_min=_as_float(raw.get("ENTRY_ACCEPT_PRIMARY_MIN_PER_MIN", ""), 15.0),
                accept_secondary_min_per_min=_as_float(
                    raw.get("ENTRY_ACCEPT_SECONDARY_MIN_PER_MIN", ""), 30.0
                ),
                accept_ratio_warn=_as_float(raw.get("ENTRY_ACCEPT_RATIO_WARN", ""), 0.25),
                accept_ratio_crit=_as_float(raw.get("ENTRY_ACCEPT_RATIO_CRIT", ""), 0.10),
                tls_handshake_warn=_as_int(raw.get("ENTRY_TLS_HANDSHAKE_WARN", ""), 5),
                tls_handshake_crit=_as_int(raw.get("ENTRY_TLS_HANDSHAKE_CRIT", ""), 20),
                io_timeout_warn=_as_int(raw.get("ENTRY_IO_TIMEOUT_WARN", ""), 10),
                io_timeout_crit=_as_int(raw.get("ENTRY_IO_TIMEOUT_CRIT", ""), 30),
                require_hop_ok=_as_bool(raw.get("ENTRY_REQUIRE_HOP_OK", ""), default=True),
            ),
        )


def _send_telegram(cfg: EntryConfig, text: str) -> bool:
    if cfg.dry_run:
        print("[DRY_RUN] entry telegram:")
        print(text)
        return True
    if not cfg.bot_token or not cfg.chat_id:
        return False
    client = TelegramClient(cfg.bot_token, proxy_url=cfg.proxy_url)
    return client.send_message_with_result(cfg.chat_id, text).success


def _write_jsonl(cfg: EntryConfig, payload: dict) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y%m%d", time.gmtime(int(payload["ts_epoch"])))
    logfile = cfg.log_dir / f"entry-{day}.jsonl"
    with logfile.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_entry_collect(env_file: Path, *, dry_run: bool = False) -> int:
    if not env_file.is_file():
        print(f"entry: config not found: {env_file}", file=os.sys.stderr)
        return 1
    raw = load_config(env_file).app.raw
    if not module_enabled("entry", raw):
        return 0
    cfg = EntryConfig.from_env(raw, dry_run=dry_run)

    now = int(time.time())
    last_ts = _load_last_ts(cfg.tick_state_path)
    interval_sec = now - last_ts if last_ts > 0 else cfg.min_interval_sec

    access_tracker = XrayAccessLogTracker(inbound_tags=cfg.inbound_tags)
    access_tracker.restore_state(cfg.access_state_path, cfg.access_log_path)
    accept_delta = access_tracker.poll()
    access_tracker.save_state(cfg.access_state_path)

    error_tracker = XrayErrorLogTracker()
    error_tracker.restore_state(cfg.error_state_path, cfg.error_log_path)
    error_delta = error_tracker.poll()
    error_tracker.save_state(cfg.error_state_path)

    hop_ok = _hop_links_ok(cfg.hop_links_raw) if cfg.hop_links_raw.strip() else True
    host = read_hostname_fqdn() or "unknown"

    primary_count = int(accept_delta.by_inbound.get(cfg.primary_inbound, 0) or 0)
    secondary_count = int(accept_delta.by_inbound.get(cfg.secondary_inbound, 0) or 0)
    primary_rate = primary_count * 60.0 / interval_sec if interval_sec > 0 else 0.0
    secondary_rate = secondary_count * 60.0 / interval_sec if interval_sec > 0 else 0.0
    ratio = (primary_rate / secondary_rate) if secondary_rate > 0 else None

    sample = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ts_epoch": now,
        "host": host,
        "sampler": "entry",
        "interval_sec": interval_sec,
        "accepts": {
            "by_inbound": accept_delta.by_inbound,
            "primary_inbound": cfg.primary_inbound,
            "secondary_inbound": cfg.secondary_inbound,
            "primary_rate_per_min": round(primary_rate, 2),
            "secondary_rate_per_min": round(secondary_rate, 2),
            "ratio": round(ratio, 4) if ratio is not None else None,
        },
        "errors": {
            "tls_handshake_delta": error_delta.delta_tls_handshake,
            "io_timeout_delta": error_delta.delta_io_timeout,
        },
        "hop_ok": hop_ok,
    }
    _write_jsonl(cfg, sample)

    mgr = StorageManager(cfg.metrics_db)
    mgr.migrate_all(raw)
    conn = mgr.open()
    try:
        insert_sample(
            conn,
            ts=now,
            interval_sec=interval_sec,
            accepts_by_inbound=accept_delta.by_inbound,
            accepts_primary_rate=primary_rate,
            accepts_secondary_rate=secondary_rate,
            accepts_ratio=ratio,
            tls_handshake_delta=error_delta.delta_tls_handshake,
            io_timeout_delta=error_delta.delta_io_timeout,
            hop_ok=hop_ok,
            details=sample,
        )
    finally:
        conn.close()

    rate_ok = interval_sec >= cfg.min_interval_sec
    pending = []
    if rate_ok:
        pending = evaluate_entry_alerts(
            host=host,
            interval_sec=interval_sec,
            accepts_by_inbound=accept_delta.by_inbound,
            primary_inbound=cfg.primary_inbound,
            secondary_inbound=cfg.secondary_inbound,
            tls_handshake_delta=error_delta.delta_tls_handshake,
            io_timeout_delta=error_delta.delta_io_timeout,
            hop_ok=hop_ok,
            thresholds=cfg.thresholds,
        )

    if cfg.alert_enable and pending:
        for alert in pending:
            conn = mgr.open()
            try:
                if not should_alert(conn, alert.alert_key, cfg.alert_cooldown_sec):
                    continue
            finally:
                conn.close()
            if not _send_telegram(cfg, alert.message):
                return 1
            conn = mgr.open()
            try:
                record_alert(
                    conn,
                    alert_type=alert.alert_type,
                    alert_key=alert.alert_key,
                    message=alert.message,
                )
            finally:
                conn.close()

    _save_last_ts(cfg.tick_state_path, now)
    return 0
