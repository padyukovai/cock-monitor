"""Incident probe collectors: ping, DNS, TCP, conntrack, hop links, systemd."""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from cock_monitor.adapters.hop_links import (
    collect_hop_links as collect_hop_links_raw,
)
from cock_monitor.adapters.hop_links import (
    resolve_hop_links_raw,
)
from cock_monitor.adapters.linux_host import (
    parse_ss_state_line_counts,
    read_conntrack_fill,
    read_sockstat_tcp,
)
from cock_monitor.modules.incident.env import get_int


def collect_conntrack() -> tuple[int, int, int]:
    return read_conntrack_fill()


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
    count = get_int("INCIDENT_PING_COUNT", 2)
    timeout_sec = get_int("INCIDENT_PING_TIMEOUT_SEC", 1)
    internal = os.environ.get("INCIDENT_PING_INTERNAL_TARGETS", "")
    external = os.environ.get("INCIDENT_PING_EXTERNAL_TARGETS", "")

    gw = default_gateway_v4()
    gw_err = "default_gateway_not_found" if not gw else ""
    int_err = "no_targets" if not internal.strip() else ""
    ext_err = "no_targets" if not external.strip() else ""

    return {
        "gateway": build_ping_group_json("gateway", gw, count, timeout_sec, gw_err),
        "internal": build_ping_group_json("internal", internal, count, timeout_sec, int_err),
        "external": build_ping_group_json("external", external, count, timeout_sec, ext_err),
    }


def tcp_probe_one(host: str, port: int, timeout_sec: int) -> tuple[int, int, str]:
    t0 = time.time_ns() // 1_000_000
    ok = 0
    err = ""
    try:
        import socket as sock

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

    timeout_sec = get_int("INCIDENT_TCP_PROBE_TIMEOUT_SEC", 2)
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


def collect_tcp_states() -> dict[str, int]:
    states = {
        "estab": 0,
        "syn_recv": 0,
        "time_wait": 0,
        "fin_wait": 0,
        "close_wait": 0,
        "orphan": 0,
    }
    try:
        out = subprocess.run(
            ["ss", "-tan"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        states.update(parse_ss_state_line_counts(out.stdout or ""))
    except (OSError, subprocess.SubprocessError):
        pass
    sock = read_sockstat_tcp()
    states["orphan"] = sock.get("orphan", 0)
    return states


def collect_hop_links() -> dict[str, Any]:
    return collect_hop_links_raw(resolve_hop_links_raw(dict(os.environ)))


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
            status = (r.stdout or "").strip() or "unknown"
            out[unit] = status
        except (OSError, subprocess.SubprocessError):
            out[unit] = "unknown"
    return out
