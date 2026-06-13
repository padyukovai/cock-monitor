"""Low-memory Telegram alerts."""

from __future__ import annotations

import os
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_STATE_FILE

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


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
class MemAlertConfig:
    enabled: bool
    warn_avail_kb: int
    crit_avail_kb: int
    cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    state_file: Path

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> MemAlertConfig:
        dry_run_cfg = _as_bool(raw.get("DRY_RUN", ""), default=False)
        state = Path(raw.get("STATE_FILE", DEFAULT_STATE_FILE))
        return cls(
            enabled=_as_bool(raw.get("MEM_ALERT_ENABLE", ""), default=False),
            warn_avail_kb=_as_int(raw.get("MEM_WARN_AVAIL_KB", ""), 150_000),
            crit_avail_kb=_as_int(raw.get("MEM_CRIT_AVAIL_KB", ""), 80_000),
            cooldown_sec=_as_int(raw.get("MEM_ALERT_COOLDOWN_SEC", ""), 600),
            dry_run=dry_run or dry_run_cfg,
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            state_file=state.parent / "mem_alert.state",
        )


def _read_mem_last_ts(path: Path) -> int:
    if not path.is_file():
        return 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("mem_last_ts="):
            val = line.split("=", 1)[1].strip()
            if val.isdigit():
                return int(val)
    return 0


def _write_mem_last_ts(path: Path, ts: int, err: TextIO) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".mem_state.") as tmp:
            tmp.write(f"mem_last_ts={ts}\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except OSError:
        err.write(f"mem_alert: cannot write state {path}\n")


def _mem_available_kb() -> int | None:
    meminfo = _safe_read(Path("/proc/meminfo"))
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemAvailable:" and parts[1].isdigit():
            return int(parts[1])
    return None


def _send_telegram(cfg: MemAlertConfig, text: str, out: TextIO, err: TextIO) -> bool:
    if cfg.dry_run:
        out.write("[DRY_RUN] Telegram message:\n")
        out.write(text + "\n")
        return True
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": cfg.chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 200:
                err.write(f"mem_alert: Telegram HTTP {resp.status}\n")
                return False
    except urllib.error.URLError as exc:
        err.write(f"mem_alert: telegram failed: {exc}\n")
        return False
    return True


def run_mem_alert(env_file: Path, *, dry_run: bool = False) -> int:
    out = os.sys.stdout
    err = os.sys.stderr
    if not env_file.is_file():
        return 0
    raw = load_config(env_file).app.raw
    cfg = MemAlertConfig.from_env(raw, dry_run=dry_run)
    if not cfg.enabled:
        return 0
    if not cfg.dry_run and (not cfg.bot_token or not cfg.chat_id):
        err.write("mem_alert: TELEGRAM_* required unless DRY_RUN\n")
        return 1

    avail = _mem_available_kb()
    if avail is None:
        return 0

    severity = 0
    if avail <= cfg.crit_avail_kb:
        severity = 2
    elif avail <= cfg.warn_avail_kb:
        severity = 1
    if severity == 0:
        return 0

    now = int(time.time())
    if (now - _read_mem_last_ts(cfg.state_file)) < cfg.cooldown_sec:
        return 0

    host = socket.getfqdn() or socket.gethostname() or "unknown"
    label = "CRITICAL" if severity == 2 else "WARNING"
    msg = (
        f"{label} low memory on {host} ({_fmt_moscow_now()})\n"
        f"MemAvailable={avail} kB "
        f"(warn<={cfg.warn_avail_kb} crit<={cfg.crit_avail_kb} kB)"
    )
    if not _send_telegram(cfg, msg, out, err):
        return 1
    _write_mem_last_ts(cfg.state_file, now, err)
    return 0
