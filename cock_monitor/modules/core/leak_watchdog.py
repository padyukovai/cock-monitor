"""Restart x-ui/xray when main xray RSS exceeds a threshold."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_STATE_FILE
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.services.leak_probe import collect_leak_probe

_MSK_TZ = "Europe/Moscow"


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


def _as_float(raw: str, default: float) -> float:
    s = (raw or "").strip()
    if not s:
        return default
    return float(s)


def _fmt_moscow_now() -> str:
    prev = os.environ.get("TZ")
    os.environ["TZ"] = _MSK_TZ
    try:
        if hasattr(time, "tzset"):
            time.tzset()
        return time.strftime("%Y-%m-%d %H:%M:%S MSK", time.localtime())
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        if hasattr(time, "tzset"):
            time.tzset()


@dataclass
class LeakWatchdogConfig:
    enabled: bool
    rss_mb: float
    cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    proxy_url: str | None
    state_file: Path
    xray_match: str
    restart_cmd: list[str]

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> LeakWatchdogConfig:
        dry_run_cfg = _as_bool(raw.get("DRY_RUN", ""), default=False)
        state = Path(raw.get("STATE_FILE", DEFAULT_STATE_FILE))
        cmd = raw.get("LEAK_WATCHDOG_RESTART_CMD", "/usr/bin/x-ui restart").strip()
        return cls(
            enabled=_as_bool(raw.get("LEAK_WATCHDOG_ENABLE", ""), default=False),
            rss_mb=_as_float(raw.get("LEAK_WATCHDOG_RSS_MB", ""), 750.0),
            cooldown_sec=_as_int(raw.get("LEAK_WATCHDOG_COOLDOWN_SEC", ""), 1800),
            dry_run=dry_run or dry_run_cfg,
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            proxy_url=raw.get("TELEGRAM_PROXY_URL", "").strip() or None,
            state_file=state.parent / "leak_watchdog.state",
            xray_match=raw.get("LEAK_XRAY_PROCESS_MATCH", "xray-linux-amd64").strip()
            or "xray-linux-amd64",
            restart_cmd=cmd.split(),
        )


def _read_last_restart_ts(path: Path) -> int:
    if not path.is_file():
        return 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("last_restart_ts="):
            val = line.split("=", 1)[1].strip()
            if val.isdigit():
                return int(val)
    return 0


def _write_last_restart_ts(path: Path, ts: int, err: TextIO) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".leak_wd.") as tmp:
            tmp.write(f"last_restart_ts={ts}\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except OSError:
        err.write(f"leak_watchdog: cannot write state {path}\n")


def _send_telegram(cfg: LeakWatchdogConfig, text: str, err: TextIO) -> bool:
    if cfg.dry_run or not cfg.bot_token or not cfg.chat_id:
        return True
    client = TelegramClient(cfg.bot_token, proxy_url=cfg.proxy_url)
    result = client.send_message_with_result(cfg.chat_id, text)
    if not result.success:
        err.write(f"leak_watchdog: telegram failed: {result.reason}\n")
        return False
    return True


def run_leak_watchdog(env_file: Path, *, dry_run: bool = False) -> int:
    out = os.sys.stdout
    err = os.sys.stderr
    if not env_file.is_file():
        return 0
    raw = load_config(env_file).app.raw
    cfg = LeakWatchdogConfig.from_env(raw, dry_run=dry_run)
    if not cfg.enabled:
        return 0

    probe = collect_leak_probe(xray_match=cfg.xray_match)
    rss = probe.xray_rss_mb
    if rss is None or rss < cfg.rss_mb:
        return 0

    now = int(time.time())
    if (now - _read_last_restart_ts(cfg.state_file)) < cfg.cooldown_sec:
        return 0

    host = socket.getfqdn() or socket.gethostname() or "unknown"
    msg = (
        f"WARNING leak watchdog on {host} ({_fmt_moscow_now()})\n"
        f"xray RSS {rss:.0f} MB >= {cfg.rss_mb:.0f} MB — restarting x-ui"
    )

    if cfg.dry_run:
        out.write("[DRY_RUN] leak_watchdog would restart x-ui:\n")
        out.write(msg + "\n")
        return 0

    if not cfg.restart_cmd or not shutil.which(cfg.restart_cmd[0]):
        err.write(f"leak_watchdog: restart command not found: {cfg.restart_cmd}\n")
        return 1

    _send_telegram(cfg, msg, err)
    try:
        proc = subprocess.run(
            cfg.restart_cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        err.write(f"leak_watchdog: restart failed: {exc}\n")
        return 1

    if proc.returncode != 0:
        err.write(
            f"leak_watchdog: restart exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()}\n"
        )
        return 1

    _write_last_restart_ts(cfg.state_file, now, err)
    out.write(f"leak_watchdog: restarted x-ui (rss was {rss:.0f} MB)\n")
    return 0
