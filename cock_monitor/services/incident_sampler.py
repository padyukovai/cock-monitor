"""Incident sampler: network/DNS/conntrack snapshots to JSONL + optional Telegram (Stage 6)."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cock_monitor.adapters.linux_host import (
    parse_ss_tan_state_counts,
    read_load_mem_from_proc,
    safe_pct,
)
from cock_monitor.env import parse_env_file

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _repo_root() -> Path:
    return _REPO_ROOT


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _get_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def apply_incident_defaults() -> None:
    """Mirror lib/incident-metrics.sh incident_apply_defaults (setdefault order matters)."""
    os.environ.setdefault("INCIDENT_SAMPLER_ENABLE", "0")
    os.environ.setdefault("INCIDENT_LOG_DIR", "/var/lib/cock-monitor")
    os.environ.setdefault("INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state")

    os.environ.setdefault("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8")
    os.environ.setdefault("INCIDENT_PING_INTERNAL_TARGETS", "")
    os.environ.setdefault("INCIDENT_PING_EXTERNAL_TARGETS", os.environ.get("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8"))
    os.environ["INCIDENT_PING_TARGETS"] = os.environ["INCIDENT_PING_EXTERNAL_TARGETS"]

    os.environ.setdefault("INCIDENT_PING_COUNT", "2")
    os.environ.setdefault("INCIDENT_PING_TIMEOUT_SEC", "1")
    os.environ.setdefault("INCIDENT_PING_LOSS_WARN_PCT", "20")
    os.environ.setdefault("INCIDENT_TCP_PROBE_LOCAL_TARGET", "127.0.0.1")
    os.environ.setdefault("INCIDENT_TCP_PROBE_EXTERNAL_TARGET", "")
    os.environ.setdefault("INCIDENT_TCP_PROBE_PORTS", "")
    os.environ.setdefault("INCIDENT_TCP_PROBE_TIMEOUT_SEC", "2")
    os.environ.setdefault("INCIDENT_TCP_PROBE_WARN_FAILS", "1")
    os.environ.setdefault("INCIDENT_TCP_PROBE_CRIT_FAILS", "0")

    os.environ.setdefault("INCIDENT_DNS_HOST", "api.telegram.org")
    os.environ.setdefault("INCIDENT_DNS_TIMEOUT_SEC", "2")
    os.environ.setdefault("INCIDENT_DNS_FAIL_STREAK_WARN", "3")

    os.environ.setdefault("INCIDENT_CONNTRACK_WARN_PCT", "85")
    os.environ.setdefault("INCIDENT_CONNTRACK_CRIT_PCT", "95")

    os.environ.setdefault("INCIDENT_SYSTEMD_UNITS", "x-ui.service")

    os.environ.setdefault("INCIDENT_ALERT_ENABLE", "0")
    os.environ.setdefault("INCIDENT_ALERT_COOLDOWN_SEC", "300")
    os.environ.setdefault("INCIDENT_POSTMORTEM_ENABLE", "1")
    os.environ.setdefault("DRY_RUN", "0")


def load_env_overwrite(path: Path) -> None:
    """Like bash `set -a; source file` — keys from file override process env."""
    raw = parse_env_file(path)
    for k, v in raw.items():
        os.environ[k] = v


def resolve_env_file(argv0: str | None) -> Path | None:
    if argv0:
        return Path(argv0).expanduser().resolve()
    ef = os.environ.get("ENV_FILE", "").strip()
    if ef:
        return Path(ef).expanduser().resolve()
    return None


def incident_hostname() -> str:
    try:
        out = subprocess.run(
            ["hostname", "-f"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        h = (out.stdout or "").strip()
        if h:
            return h
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname() or "unknown-host"


def sysctl_int(name: str) -> int | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if out.returncode != 0:
            return None
        v = (out.stdout or "").strip()
        if re.fullmatch(r"[0-9]+", v or ""):
            return int(v)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def collect_conntrack() -> tuple[int, int, int]:
    count = sysctl_int("net.netfilter.nf_conntrack_count") or 0
    maxv = sysctl_int("net.netfilter.nf_conntrack_max") or 0
    fill = safe_pct(count, maxv)
    return count, maxv, fill


def collect_dns(host: str, timeout_sec: int) -> tuple[int, int, str]:
    t0 = time.time_ns() // 1_000_000
    ok = 0
    err = ""
    try:
        r = subprocess.run(
            ["timeout", f"{timeout_sec}s", "getent", "ahostsv4", host],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 2,
            check=False,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        ok = 1 if r.returncode == 0 else 0
        if ok == 0:
            err = f"lookup_failed_rc_{r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        ok = 0
        err = f"lookup_failed_rc_{getattr(e, 'errno', -1)}"
    t1 = time.time_ns() // 1_000_000
    lat = max(0, t1 - t0) if t1 >= t0 else 0
    return ok, lat, err


def parse_ping_output(text: str) -> tuple[int, int, int, float]:
    tx = rx = loss = 0
    avg = 0.0
    m = re.search(r"(\d+) packets transmitted, (\d+) received", text)
    if m:
        tx, rx = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)% packet loss", text)
    if m:
        loss = int(m.group(1))
    m = re.search(r"rtt min/avg/max[^=]+=\s*[\d.]+/([\d.]+)/", text)
    if m:
        try:
            avg = float(m.group(1))
        except ValueError:
            avg = 0.0
    return tx, rx, loss, avg


def ping_one(target: str, count: int, timeout_sec: int) -> tuple[int, int, int, float]:
    try:
        out = subprocess.run(
            ["ping", "-n", "-c", str(count), "-W", str(timeout_sec), target],
            capture_output=True,
            text=True,
            timeout=count * (timeout_sec + 1) + 5,
            check=False,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        text = (out.stdout or "") + (out.stderr or "")
        return parse_ping_output(text)
    except (OSError, subprocess.SubprocessError):
        return 0, 0, 100, 0.0


def collect_ping_legacy(targets: str, count: int, timeout_sec: int) -> tuple[list[dict[str, Any]], int]:
    """Legacy top-level ping list (INCIDENT_PING_TARGETS after external override)."""
    arr: list[dict[str, Any]] = []
    max_loss = 0
    for target in targets.split():
        tx, rx, loss, avg = ping_one(target, count, timeout_sec)
        if loss > max_loss:
            max_loss = loss
        arr.append(
            {
                "target": target,
                "tx": tx,
                "rx": rx,
                "loss_pct": loss,
                "avg_ms": avg,
            }
        )
    return arr, max_loss


def default_gateway_v4() -> str:
    try:
        out = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        line = (out.stdout or "").strip().splitlines()
        if line:
            parts = line[0].split()
            for i, p in enumerate(parts):
                if p == "via" and i + 1 < len(parts):
                    return parts[i + 1]
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        line = (out.stdout or "").strip().splitlines()
        if line:
            parts = line[0].split()
            for i, p in enumerate(parts):
                if p == "via" and i + 1 < len(parts):
                    return parts[i + 1]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def collect_ping_group(
    targets: str,
    count: int,
    timeout_sec: int,
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    """Returns checks, targets_total, targets_failed, max_loss, avg_loss."""
    checks: list[dict[str, Any]] = []
    if not targets.strip():
        return checks, 0, 0, 0, 0
    total = 0
    failed = 0
    sum_loss = 0
    max_loss = 0
    for target in targets.split():
        total += 1
        tx, rx, loss, avg = ping_one(target, count, timeout_sec)
        if loss >= 100:
            failed += 1
        sum_loss += loss
        if loss > max_loss:
            max_loss = loss
        checks.append(
            {
                "target": target,
                "tx": tx,
                "rx": rx,
                "loss_pct": loss,
                "avg_ms": avg,
            }
        )
    avg_loss = sum_loss // total if total > 0 else 0
    return checks, total, failed, max_loss, avg_loss


def build_ping_group_json(
    group_name: str,
    targets: str,
    count: int,
    timeout_sec: int,
    group_error: str,
) -> dict[str, Any]:
    checks, tt, tf, max_loss, avg_loss = collect_ping_group(targets, count, timeout_sec)
    return {
        "checks": checks,
        "rollup": {
            "targets_total": tt,
            "targets_failed": tf,
            "max_loss_pct": max_loss,
            "avg_loss_pct": avg_loss,
        },
        "error": group_error,
    }


def collect_ping_groups() -> dict[str, Any]:
    count = _get_int("INCIDENT_PING_COUNT", 2)
    timeout_sec = _get_int("INCIDENT_PING_TIMEOUT_SEC", 1)
    internal = os.environ.get("INCIDENT_PING_INTERNAL_TARGETS", "")
    external = os.environ.get("INCIDENT_PING_EXTERNAL_TARGETS", "")

    gw = default_gateway_v4()
    gw_err = ""
    if not gw:
        gw_err = "default_gateway_not_found"

    int_err = "no_targets" if not internal.strip() else ""
    ext_err = "no_targets" if not external.strip() else ""

    gateway = build_ping_group_json("gateway", gw, count, timeout_sec, gw_err)
    internal_j = build_ping_group_json("internal", internal, count, timeout_sec, int_err)
    external_j = build_ping_group_json("external", external, count, timeout_sec, ext_err)

    return {"gateway": gateway, "internal": internal_j, "external": external_j}


def tcp_probe_one(host: str, port: int, timeout_sec: int) -> tuple[int, int, str]:
    t0 = time.time_ns() // 1_000_000
    ok = 0
    err = ""
    try:
        import socket as sock

        deadline = time.monotonic() + timeout_sec
        s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
        s.settimeout(timeout_sec)
        try:
            s.connect((host, port))
            ok = 1
        except OSError:
            ok = 0
            err = "connect_failed"
        finally:
            s.close()
    except OSError:
        ok = 0
        err = "connect_failed"
    t1 = time.time_ns() // 1_000_000
    lat = max(0, t1 - t0) if t1 >= t0 else 0
    return ok, lat, err


def collect_tcp_probes() -> dict[str, Any]:
    ports_raw = os.environ.get("INCIDENT_TCP_PROBE_PORTS", "").strip()
    if not ports_raw:
        return {
            "enabled": 0,
            "targets": {"local": "", "external": ""},
            "totals": {
                "all": {"total": 0, "fails": 0},
                "local": {"total": 0, "fails": 0},
                "external": {"total": 0, "fails": 0},
            },
            "checks": [],
        }

    timeout_sec = _get_int("INCIDENT_TCP_PROBE_TIMEOUT_SEC", 2)
    local_t = os.environ.get("INCIDENT_TCP_PROBE_LOCAL_TARGET", "127.0.0.1")
    ext_t = os.environ.get("INCIDENT_TCP_PROBE_EXTERNAL_TARGET", "")

    checks: list[dict[str, Any]] = []
    total = fails = 0
    local_total = local_fails = 0
    ext_total = ext_fails = 0

    for scope, target in (("local", local_t), ("external", ext_t)):
        if not str(target).strip():
            continue
        for port_str in ports_raw.split():
            try:
                port = int(port_str)
            except ValueError:
                continue
            total += 1
            if scope == "local":
                local_total += 1
            else:
                ext_total += 1
            ok, lat, err = tcp_probe_one(target.strip(), port, timeout_sec)
            if ok == 0:
                fails += 1
                if scope == "local":
                    local_fails += 1
                else:
                    ext_fails += 1
            checks.append(
                {
                    "scope": scope,
                    "target": target,
                    "port": port,
                    "ok": ok,
                    "latency_ms": lat,
                    "error": err,
                }
            )

    return {
        "enabled": 1,
        "targets": {"local": local_t, "external": ext_t},
        "totals": {
            "all": {"total": total, "fails": fails},
            "local": {"total": local_total, "fails": local_fails},
            "external": {"total": ext_total, "fails": ext_fails},
        },
        "checks": checks,
    }


def collect_ss() -> tuple[int, int, int]:
    try:
        out = subprocess.run(
            ["ss", "-tan"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return parse_ss_tan_state_counts(out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return 0, 0, 0


def collect_units() -> dict[str, str]:
    units = os.environ.get("INCIDENT_SYSTEMD_UNITS", "x-ui.service")

    out: dict[str, str] = {}
    for unit in units.split():
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            status = (r.stdout or "").strip()
            if not status:
                status = "unknown"
            out[unit] = status
        except (OSError, subprocess.SubprocessError):
            out[unit] = "unknown"
    return out


def state_load(path: Path) -> dict[str, str]:
    out = {
        "last_level": "OK",
        "last_alert_ts": "0",
        "dns_fail_streak": "0",
        "incident_active": "0",
        "incident_start_ts": "0",
        "incident_peak_level": "OK",
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
    )
    tmp = path.parent / f".incident-state.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def send_telegram(text: str, parse_mode: str | None = None) -> None:
    if os.environ.get("DRY_RUN", "0") == "1":
        print("[DRY_RUN] incident telegram:")
        if parse_mode:
            print(f"parse_mode={parse_mode}")
        print(text)
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    args = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-X",
        "POST",
        url,
        "--data-urlencode",
        f"chat_id={chat}",
        "--data-urlencode",
        "disable_web_page_preview=true",
    ]
    if parse_mode:
        args.extend(["--data-urlencode", f"parse_mode={parse_mode}"])
    args.extend(["--data-urlencode", f"text={text}"])
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def compute_level(
    *,
    fill_pct: int,
    conn_warn: int,
    conn_crit: int,
    ping_max_loss: int,
    ping_loss_warn: int,
    dns_fail_streak: int,
    dns_streak_warn: int,
    tcp_enabled: int,
    tcp_fails: int,
    tcp_warn_fail: int,
    tcp_crit_fail: int,
) -> str:
    level = "OK"
    if fill_pct >= conn_crit:
        level = "CRIT"
    elif tcp_enabled == 1 and tcp_crit_fail > 0 and tcp_fails >= tcp_crit_fail:
        level = "CRIT"
    elif (
        fill_pct >= conn_warn
        or ping_max_loss >= ping_loss_warn
        or dns_fail_streak >= dns_streak_warn
        or (tcp_enabled == 1 and tcp_fails >= tcp_warn_fail)
    ):
        level = "WARN"
    return level


def incident_track_and_postmortem(
    old_level: str,
    new_level: str,
    now_ts: int,
    host: str,
    st: dict[str, str],
    log_dir: Path,
) -> None:
    if old_level == "OK" and new_level != "OK":
        st["incident_active"] = "1"
        st["incident_start_ts"] = str(now_ts)
        st["incident_peak_level"] = new_level
    elif old_level != "OK" and new_level != "OK":
        st["incident_active"] = "1"
        if new_level == "CRIT":
            st["incident_peak_level"] = "CRIT"
        elif st.get("incident_peak_level") != "CRIT" and new_level == "WARN":
            st["incident_peak_level"] = "WARN"
    elif old_level != "OK" and new_level == "OK":
        if st.get("incident_active") == "1":
            if os.environ.get("INCIDENT_POSTMORTEM_ENABLE", "1") == "1":
                pm = _repo_root() / "bin" / "incident-postmortem.py"
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
                    send_telegram(body, parse_mode="HTML")
            st["incident_active"] = "0"
            st["incident_start_ts"] = "0"
            st["incident_peak_level"] = "OK"


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
    last_alert_ts = int(st.get("last_alert_ts", "0") or "0")
    cooldown = _get_int("INCIDENT_ALERT_COOLDOWN_SEC", 300)
    changed = 1 if level != last else 0
    cooldown_due = 1 if (now_ts - last_alert_ts >= cooldown) else 0
    if (changed or cooldown_due) and (level != "OK" or last != "OK"):
        send_telegram(snapshot_text)
        st["last_alert_ts"] = str(now_ts)


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
        "sampler": "incident-sampler",
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
        },
        "tcp_probe": tcp_probe,
        "load1": load_val,
        "mem_avail_kb": mem_kb,
        "units": units,
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"


def run_once() -> int:
    """Single sampler tick (after env loaded)."""
    apply_incident_defaults()
    if os.environ.get("INCIDENT_SAMPLER_ENABLE", "0") != "1":
        return 0

    now_ts = int(time.time())
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    host = incident_hostname()
    log_dir = Path(os.environ.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor"))
    state_path = Path(os.environ.get("INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state"))

    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"incident-{time.strftime('%Y%m%d', time.gmtime(now_ts))}.jsonl"

    st = state_load(state_path)
    old_level = st.get("last_level", "OK")

    ping_count = _get_int("INCIDENT_PING_COUNT", 2)
    ping_timeout = _get_int("INCIDENT_PING_TIMEOUT_SEC", 1)
    ping_targets = os.environ.get("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8")

    ping_arr, ping_max_loss = collect_ping_legacy(ping_targets, ping_count, ping_timeout)
    ping_groups = collect_ping_groups()

    dns_host = os.environ.get("INCIDENT_DNS_HOST", "api.telegram.org")
    dns_timeout = _get_int("INCIDENT_DNS_TIMEOUT_SEC", 2)
    dns_ok, dns_lat, dns_err = collect_dns(dns_host, dns_timeout)

    dns_streak = int(st.get("dns_fail_streak", "0") or "0")
    if dns_ok == 1:
        dns_streak = 0
    else:
        dns_streak += 1
    st["dns_fail_streak"] = str(dns_streak)

    ct_count, ct_max, ct_fill = collect_conntrack()
    tcp_estab, tcp_syn, tcp_tw = collect_ss()
    tcp_probe = collect_tcp_probes()

    load1, mem_kb = read_load_mem_from_proc()
    units = collect_units()

    # Level
    tcp_en = int(tcp_probe.get("enabled", 0) or 0)
    tcp_total = int(tcp_probe.get("totals", {}).get("all", {}).get("total", 0) or 0)
    tcp_fails = int(tcp_probe.get("totals", {}).get("all", {}).get("fails", 0) or 0)
    tcp_warn = _get_int("INCIDENT_TCP_PROBE_WARN_FAILS", 1)
    tcp_crit = _get_int("INCIDENT_TCP_PROBE_CRIT_FAILS", 0)

    level = compute_level(
        fill_pct=ct_fill,
        conn_warn=_get_int("INCIDENT_CONNTRACK_WARN_PCT", 85),
        conn_crit=_get_int("INCIDENT_CONNTRACK_CRIT_PCT", 95),
        ping_max_loss=ping_max_loss,
        ping_loss_warn=_get_int("INCIDENT_PING_LOSS_WARN_PCT", 20),
        dns_fail_streak=dns_streak,
        dns_streak_warn=_get_int("INCIDENT_DNS_FAIL_STREAK_WARN", 3),
        tcp_enabled=tcp_en,
        tcp_fails=tcp_fails,
        tcp_warn_fail=tcp_warn,
        tcp_crit_fail=tcp_crit,
    )

    line = build_json_line(
        ts_iso=ts_iso,
        ts_epoch=now_ts,
        host=host,
        level=level,
        ping=ping_arr,
        ping_groups=ping_groups,
        dns_host=dns_host,
        dns_ok=dns_ok,
        dns_lat=dns_lat,
        dns_err=dns_err,
        ct_count=ct_count,
        ct_max=ct_max,
        ct_fill=ct_fill,
        tcp_estab=tcp_estab,
        tcp_syn=tcp_syn,
        tcp_tw=tcp_tw,
        tcp_probe=tcp_probe,
        load1=load1,
        mem_kb=mem_kb,
        units=units,
    )

    with logfile.open("a", encoding="utf-8") as f:
        f.write(line)

    incident_track_and_postmortem(old_level, level, now_ts, host, st, log_dir)

    snap = (
        f"incident-sampler {level} on {host}\n"
        f"time: {ts_iso}\n"
        f"conntrack: {ct_count}/{ct_max} ({ct_fill}%)\n"
        f"ping max loss: {ping_max_loss}%\n"
        f"dns: ok={dns_ok} streak={dns_streak} err={dns_err}\n"
        f"tcp: estab={tcp_estab} syn_recv={tcp_syn} tw={tcp_tw}\n"
        f"tcp-probe all: {tcp_fails}/{tcp_total} failed\n"
        f"tcp-probe local: {tcp_probe['totals']['local']['fails']}/{tcp_probe['totals']['local']['total']} "
        f"target={tcp_probe['targets']['local']}\n"
        f"tcp-probe external: {tcp_probe['totals']['external']['fails']}/{tcp_probe['totals']['external']['total']} "
        f"target={tcp_probe['targets']['external']}"
    )
    maybe_alert(now_ts, level, st, snapshot_text=snap)

    st["last_level"] = level
    state_save(state_path, st)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 1 and argv[0] in ("-h", "--help"):
        print("Usage: ENV_FILE=/path/to.env incident-sampler.sh", file=sys.stderr)
        print("   or: incident-sampler.sh /path/to.env", file=sys.stderr)
        return 2
    env_path = resolve_env_file(argv[0] if argv else None)
    if env_path is None:
        print("incident-sampler: missing env file path", file=sys.stderr)
        return 2
    if not env_path.is_file():
        print(f"incident-sampler: config not found: {env_path}", file=sys.stderr)
        return 1
    load_env_overwrite(env_path)
    try:
        return run_once()
    except OSError as e:
        print(f"incident-sampler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
