"""WG collect tick: sample, store, alert."""

from __future__ import annotations

import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.modules.wg.collector import collect_wg_snapshot
from cock_monitor.modules.wg.storage import insert_sample, last_alert_ts, peers_to_json, record_alert
from cock_monitor.platform.registry import module_enabled
from cock_monitor.platform.storage.manager import StorageManager


def _as_bool(raw: str, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s not in {"0", "false", "False", "no", "NO"}


def _as_int(raw: str, default: int) -> int:
    s = (raw or "").strip()
    if not s:
        return default
    return int(s)


@dataclass(frozen=True)
class WgConfig:
    interface: str
    stale_handshake_sec: int
    max_peers: int
    alert_cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    metrics_db: Path

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> WgConfig:
        return cls(
            interface=raw.get("WG_INTERFACE", "wg0").strip() or "wg0",
            stale_handshake_sec=_as_int(raw.get("WG_STALE_HANDSHAKE_SEC", ""), 180),
            max_peers=_as_int(raw.get("WG_MAX_PEERS", ""), 0),
            alert_cooldown_sec=_as_int(raw.get("WG_ALERT_COOLDOWN_SEC", ""), 600),
            dry_run=dry_run or _as_bool(raw.get("DRY_RUN", ""), default=False),
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            metrics_db=Path(raw.get("METRICS_DB", DEFAULT_METRICS_DB)),
        )


def _send_telegram(cfg: WgConfig, text: str) -> bool:
    if cfg.dry_run:
        print("[DRY_RUN] Telegram message:")
        print(text)
        return True
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": cfg.chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


def run_wg_collect(env_file: Path, *, dry_run: bool = False) -> int:
    if not env_file.is_file():
        return 1
    raw = load_config(env_file).app.raw
    if not module_enabled("wg", raw):
        return 0
    cfg = WgConfig.from_env(raw, dry_run=dry_run)
    snap = collect_wg_snapshot(cfg.interface)
    if snap is None:
        print(f"wg: cannot read interface {cfg.interface}", file=os.sys.stderr)
        return 1

    now = int(time.time())
    stale = snap.stale_count(cfg.stale_handshake_sec)
    mgr = StorageManager(cfg.metrics_db)
    mgr.migrate_all(raw)
    conn = mgr.open()
    try:
        insert_sample(
            conn,
            ts=now,
            peer_count=snap.peer_count,
            total_rx=snap.total_rx,
            total_tx=snap.total_tx,
            stale_count=stale,
            peers_json=peers_to_json(snap.peers),
        )
    finally:
        conn.close()

    host = socket.getfqdn() or socket.gethostname() or "unknown"
    pending: list[tuple[str, str]] = []
    if stale > 0:
        pending.append(
            ("stale_handshake", f"WG stale handshake on {host}: {stale} peer(s) > {cfg.stale_handshake_sec}s")
        )
    if cfg.max_peers > 0 and snap.peer_count > cfg.max_peers:
        pending.append(
            ("max_peers", f"WG peer count on {host}: {snap.peer_count} > limit {cfg.max_peers}")
        )

    for alert_key, msg in pending:
        conn = mgr.open()
        try:
            if not last_alert_ts(conn, alert_key, cfg.alert_cooldown_sec):
                continue
        finally:
            conn.close()
        if not _send_telegram(cfg, msg):
            return 1
        conn = mgr.open()
        try:
            record_alert(conn, alert_type=alert_key, alert_key=alert_key, message=msg)
        finally:
            conn.close()

    return 0


def wg_status_text(env_file: Path) -> str:
    from cock_monitor.modules.wg.collector import format_status

    raw = load_config(env_file).app.raw
    cfg = WgConfig.from_env(raw, dry_run=False)
    snap = collect_wg_snapshot(cfg.interface)
    if snap is None:
        return f"WireGuard: cannot read {cfg.interface}"
    return format_status(snap, stale_sec=cfg.stale_handshake_sec)
