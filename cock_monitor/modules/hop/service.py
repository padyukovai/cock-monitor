"""Hop collect tick: ss links, error.log, optional probe, store, alert."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cock_monitor.adapters.hop_links import collect_hop_links, resolve_hop_links_raw
from cock_monitor.adapters.linux_host import read_hostname_fqdn
from cock_monitor.adapters.xray_error_log import XrayErrorLogTracker
from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.modules.hop.alerts import HopAlertThresholds, evaluate_hop_alerts
from cock_monitor.modules.hop.probe import parse_hop_probes_env, run_hop_probe
from cock_monitor.modules.hop.storage import insert_sample, record_alert, should_alert
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


@dataclass(frozen=True)
class HopConfig:
    hop_links_raw: str
    error_log_path: Path
    error_state_path: Path
    probe_enable: bool
    probes_raw: str
    probe_parallel: int
    probe_timeout_sec: int
    alert_enable: bool
    alert_cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    proxy_url: str | None
    metrics_db: Path
    thresholds: HopAlertThresholds

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> HopConfig:
        links = resolve_hop_links_raw(raw)
        state_dir = Path(raw.get("HOP_STATE_DIR", "/var/lib/cock-monitor"))
        return cls(
            hop_links_raw=links,
            error_log_path=Path(raw.get("HOP_ERROR_LOG_PATH", "/var/log/x-ui/error.log")),
            error_state_path=state_dir / "hop_error_log.state",
            probe_enable=_as_bool(raw.get("HOP_PROBE_ENABLE", ""), default=False),
            probes_raw=raw.get("HOP_PROBES", "").strip(),
            probe_parallel=_as_int(raw.get("HOP_PROBE_PARALLEL", ""), 10),
            probe_timeout_sec=_as_int(raw.get("HOP_PROBE_TIMEOUT_SEC", ""), 15),
            alert_enable=_as_bool(raw.get("HOP_ALERT_ENABLE", ""), default=False),
            alert_cooldown_sec=_as_int(raw.get("HOP_ALERT_COOLDOWN_SEC", ""), 600),
            dry_run=dry_run or _as_bool(raw.get("DRY_RUN", ""), default=False),
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            proxy_url=raw.get("TELEGRAM_PROXY_URL", "").strip() or None,
            metrics_db=Path(raw.get("METRICS_DB", DEFAULT_METRICS_DB)),
            thresholds=HopAlertThresholds(
                estab_warn=_as_int(raw.get("HOP_ESTAB_WARN", ""), 5),
                estab_crit=_as_int(raw.get("HOP_ESTAB_CRIT", ""), 20),
                fin_wait_warn=_as_int(raw.get("HOP_FIN_WAIT_WARN", ""), 20),
                fin_wait_crit=_as_int(raw.get("HOP_FIN_WAIT_CRIT", ""), 50),
                error_delta_warn=_as_int(raw.get("HOP_ERROR_DELTA_WARN", ""), 3),
                error_delta_crit=_as_int(raw.get("HOP_ERROR_DELTA_CRIT", ""), 10),
                probe_success_warn_pct=_as_int(raw.get("HOP_PROBE_SUCCESS_WARN_PCT", ""), 80),
                probe_success_crit_pct=_as_int(raw.get("HOP_PROBE_SUCCESS_CRIT_PCT", ""), 50),
            ),
        )


def _send_telegram(cfg: HopConfig, text: str) -> bool:
    if cfg.dry_run:
        print("[DRY_RUN] hop telegram:")
        print(text)
        return True
    if not cfg.bot_token or not cfg.chat_id:
        return False
    client = TelegramClient(cfg.bot_token, proxy_url=cfg.proxy_url)
    return client.send_message_with_result(cfg.chat_id, text).success


def _collect_error_delta(cfg: HopConfig) -> dict[str, Any]:
    tracker = XrayErrorLogTracker()
    tracker.restore_state(cfg.error_state_path, cfg.error_log_path)
    delta = tracker.poll()
    tracker.save_state(cfg.error_state_path)
    return {
        "delta_lines": delta.delta_lines,
        "delta_total": delta.delta_total,
        "delta_mux_fail": delta.delta_mux_fail,
        "delta_conn_refused": delta.delta_conn_refused,
        "delta_retry_exhausted": delta.delta_retry_exhausted,
        "tail": delta.tail,
    }


def _run_probes(cfg: HopConfig) -> list[dict[str, Any]]:
    if not cfg.probe_enable or not cfg.probes_raw.strip():
        return []
    results: list[dict[str, Any]] = []
    for spec in parse_hop_probes_env(cfg.probes_raw):
        results.append(
            run_hop_probe(
                spec,
                parallel=cfg.probe_parallel,
                timeout_sec=cfg.probe_timeout_sec,
            )
        )
    return results


def _probe_for_link(probes: list[dict[str, Any]], link_name: str) -> dict[str, Any] | None:
    for probe in probes:
        if str(probe.get("name")) == link_name:
            return probe
    return None


def run_hop_collect(env_file: Path, *, dry_run: bool = False) -> int:
    if not env_file.is_file():
        print(f"hop: config not found: {env_file}", file=os.sys.stderr)
        return 1
    raw = load_config(env_file).app.raw
    if not module_enabled("hop", raw):
        return 0
    cfg = HopConfig.from_env(raw, dry_run=dry_run)
    if not cfg.hop_links_raw.strip():
        print("hop: HOP_LINKS empty, nothing to collect", file=os.sys.stderr)
        return 0

    host = read_hostname_fqdn() or socket.getfqdn() or "unknown"
    now = int(time.time())
    hop_data = collect_hop_links(cfg.hop_links_raw)
    error_delta = _collect_error_delta(cfg)
    probes = _run_probes(cfg)

    mgr = StorageManager(cfg.metrics_db)
    mgr.migrate_all(raw)
    conn = mgr.open()
    try:
        for link in hop_data.get("links", []):
            pname = str(link.get("name") or "hop")
            probe = _probe_for_link(probes, pname)
            details = {
                "host": host,
                "link": link,
                "error_delta": error_delta,
                "probe": probe,
            }
            insert_sample(
                conn,
                ts=now,
                link_name=pname,
                estab=int(link.get("estab", 0) or 0),
                fin_wait=int(link.get("fin_wait", 0) or 0),
                time_wait=int(link.get("time_wait", 0) or 0),
                link_error=str(link.get("error") or ""),
                error_delta_total=int(error_delta.get("delta_total", 0) or 0),
                error_delta_mux=int(error_delta.get("delta_mux_fail", 0) or 0),
                error_delta_refused=int(error_delta.get("delta_conn_refused", 0) or 0),
                error_delta_retry=int(error_delta.get("delta_retry_exhausted", 0) or 0),
                probe_ok=int(probe["ok"]) if probe else None,
                probe_total=int(probe["total"]) if probe else None,
                probe_latency_p50_ms=int(probe.get("latency_p50_ms", 0) or 0) if probe else None,
                details=details,
            )
    finally:
        conn.close()

    if not cfg.alert_enable:
        return 0

    pending = evaluate_hop_alerts(
        host=host,
        links=hop_data.get("links", []),
        error_delta=error_delta,
        probes=probes,
        thresholds=cfg.thresholds,
    )
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
            record_alert(conn, alert_type=alert.alert_type, alert_key=alert.alert_key, message=alert.message)
        finally:
            conn.close()

    return 0


def hop_status_text(env_file: Path) -> str:
    raw = load_config(env_file).app.raw
    cfg = HopConfig.from_env(raw, dry_run=False)
    hop_data = collect_hop_links(cfg.hop_links_raw)
    lines = [f"Hop links ({read_hostname_fqdn()}):"]
    for link in hop_data.get("links", []):
        err = link.get("error") or ""
        err_s = f" err={err}" if err else ""
        lines.append(
            f"  {link.get('name')}: estab={link.get('estab')} fin_wait={link.get('fin_wait')} "
            f"tw={link.get('time_wait')}{err_s}"
        )
    if cfg.probe_enable and cfg.probes_raw.strip():
        lines.append("  (probes enabled; run collect tick for results)")
    return "\n".join(lines)
