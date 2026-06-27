"""Incident state, Telegram alerts, and post-mortem on recovery."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from cock_monitor.modules.incident.env import get_int
from cock_monitor.platform.telegram.client import DeliveryResult, TelegramClient

_REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    return _REPO_ROOT


def state_load(path: Path) -> dict[str, str]:
    out = {
        "last_level": "OK",
        "last_alert_ts": "0",
        "dns_fail_streak": "0",
        "incident_active": "0",
        "incident_start_ts": "0",
        "incident_peak_level": "OK",
        "non_ok_streak": "0",
        "non_ok_first_ts": "0",
        "non_ok_peak_level": "OK",
    }
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if k in out:
                out[k] = v
    except OSError:
        pass
    return out


def state_save(path: Path, st: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        f"last_level={st['last_level']}\n"
        f"last_alert_ts={st['last_alert_ts']}\n"
        f"dns_fail_streak={st['dns_fail_streak']}\n"
        f"incident_active={st['incident_active']}\n"
        f"incident_start_ts={st['incident_start_ts']}\n"
        f"incident_peak_level={st['incident_peak_level']}\n"
        f"non_ok_streak={st['non_ok_streak']}\n"
        f"non_ok_first_ts={st['non_ok_first_ts']}\n"
        f"non_ok_peak_level={st['non_ok_peak_level']}\n"
    )
    tmp = path.parent / f".incident-state.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def send_telegram(text: str, parse_mode: str | None = None) -> DeliveryResult:
    if os.environ.get("DRY_RUN", "0") == "1":
        print("[DRY_RUN] incident telegram:")
        if parse_mode:
            print(f"parse_mode={parse_mode}")
        print(text)
        return DeliveryResult(success=True, reason="dry_run", attempts=1)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    proxy = os.environ.get("TELEGRAM_PROXY_URL", "").strip() or None
    if not token or not chat:
        return DeliveryResult(success=False, reason="telegram_not_configured", attempts=1)
    return TelegramClient(token, proxy_url=proxy).send_message_with_result(chat, text, parse_mode=parse_mode or "")


def incident_track_and_postmortem(
    old_level: str,
    new_level: str,
    now_ts: int,
    host: str,
    st: dict[str, str],
    log_dir: Path,
) -> None:
    warn_consecutive = max(1, get_int("INCIDENT_WARN_CONSECUTIVE", 1))

    if new_level != "OK":
        streak = int(st.get("non_ok_streak", "0") or "0") + 1
        st["non_ok_streak"] = str(streak)
        if streak == 1:
            st["non_ok_first_ts"] = str(now_ts)
            st["non_ok_peak_level"] = new_level
        else:
            if new_level == "CRIT":
                st["non_ok_peak_level"] = "CRIT"
            elif st.get("non_ok_peak_level") != "CRIT" and new_level == "WARN":
                st["non_ok_peak_level"] = "WARN"

        if st.get("incident_active") != "1" and streak >= warn_consecutive:
            st["incident_active"] = "1"
            st["incident_start_ts"] = st.get("non_ok_first_ts", str(now_ts))
            st["incident_peak_level"] = st.get("non_ok_peak_level", new_level)
        elif st.get("incident_active") == "1":
            if new_level == "CRIT":
                st["incident_peak_level"] = "CRIT"
            elif st.get("incident_peak_level") != "CRIT" and new_level == "WARN":
                st["incident_peak_level"] = "WARN"
    elif old_level != "OK" and new_level == "OK":
        if st.get("incident_active") == "1":
            if os.environ.get("INCIDENT_POSTMORTEM_ENABLE", "1") == "1":
                pm = repo_root() / "bin" / "incident-postmortem.py"
                if pm.is_file():
                    try:
                        start_ts = int(st.get("incident_start_ts", "0") or "0")
                        peak = st.get("incident_peak_level", "OK") or "OK"
                        r = subprocess.run(
                            [
                                sys.executable,
                                str(pm),
                                str(start_ts),
                                str(now_ts),
                                str(log_dir),
                                host,
                                peak,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=60,
                            check=False,
                        )
                        body = (r.stdout or "").strip() or "<i>incident-postmortem.py failed</i>"
                    except (OSError, subprocess.SubprocessError, ValueError):
                        body = "<i>incident-postmortem.py failed</i>"
                    result = send_telegram(body, parse_mode="HTML")
                    if not result.success:
                        print(
                            f"incident: telegram send failed ({result.reason})",
                            file=sys.stderr,
                        )
            st["incident_active"] = "0"
            st["incident_start_ts"] = "0"
            st["incident_peak_level"] = "OK"
        st["non_ok_streak"] = "0"
        st["non_ok_first_ts"] = "0"
        st["non_ok_peak_level"] = "OK"


def maybe_alert(
    now_ts: int,
    level: str,
    st: dict[str, str],
    *,
    snapshot_text: str,
) -> None:
    if os.environ.get("INCIDENT_ALERT_ENABLE", "0") != "1":
        return
    last = st.get("last_level", "OK")
    warn_consecutive = max(1, get_int("INCIDENT_WARN_CONSECUTIVE", 1))
    non_ok_streak = int(st.get("non_ok_streak", str(warn_consecutive)) or str(warn_consecutive))
    if level != "OK" and non_ok_streak < warn_consecutive:
        return
    last_alert_ts = int(st.get("last_alert_ts", "0") or "0")
    cooldown = get_int("INCIDENT_ALERT_COOLDOWN_SEC", 300)
    changed = 1 if level != last else 0
    cooldown_due = 1 if (now_ts - last_alert_ts >= cooldown) else 0
    if (changed or cooldown_due) and (level != "OK" or last != "OK"):
        result = send_telegram(snapshot_text)
        if result.success:
            st["last_alert_ts"] = str(now_ts)
            print("incident: telegram alert sent", file=sys.stderr)
        else:
            print(
                f"incident: telegram alert failed ({result.reason})",
                file=sys.stderr,
            )


def build_json_line(
    *,
    ts_iso: str,
    ts_epoch: int,
    host: str,
    level: str,
    ping: list[dict[str, Any]],
    ping_groups: dict[str, Any],
    dns_host: str,
    dns_ok: int,
    dns_lat: int,
    dns_err: str,
    ct_count: int,
    ct_max: int,
    ct_fill: int,
    tcp_estab: int,
    tcp_syn: int,
    tcp_tw: int,
    tcp_fin_wait: int,
    tcp_close_wait: int,
    tcp_orphan: int,
    hop_links: dict[str, Any],
    tcp_probe: dict[str, Any],
    load1: str,
    mem_kb: int,
    units: dict[str, str],
) -> str:
    load_val: float | str = load1
    if re.match(r"^[0-9]+(\.[0-9]+)?$", str(load1)):
        try:
            load_val = float(load1)
        except ValueError:
            load_val = load1
    row: dict[str, Any] = {
        "ts": ts_iso,
        "ts_epoch": ts_epoch,
        "host": host,
        "sampler": "incident",
        "version": "1",
        "level": level,
        "ping": ping,
        "ping_groups": ping_groups,
        "dns": {
            "host": dns_host,
            "ok": dns_ok,
            "latency_ms": dns_lat,
            "error": dns_err,
        },
        "conntrack": {
            "count": ct_count,
            "max": ct_max,
            "fill_pct": ct_fill,
        },
        "tcp": {
            "estab": tcp_estab,
            "syn_recv": tcp_syn,
            "time_wait": tcp_tw,
            "fin_wait": tcp_fin_wait,
            "close_wait": tcp_close_wait,
            "orphan": tcp_orphan,
        },
        "hop_links": hop_links,
        "tcp_probe": tcp_probe,
        "load1": load_val,
        "mem_avail_kb": mem_kb,
        "units": units,
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
