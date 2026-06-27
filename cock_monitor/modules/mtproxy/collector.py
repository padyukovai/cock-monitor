from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any


def _peer_ip_from_ss_line(line: str) -> str | None:
    parts = line.split()
    if len(parts) < 2:
        return None
    peer = parts[-1]
    if peer.startswith("["):
        if "]:" not in peer:
            return None
        host = peer[1 : peer.rfind("]")]
        port = peer[peer.rfind("]") + 2 :]
    else:
        if peer.count(":") != 1:
            return None
        host, port = peer.rsplit(":", 1)
    if not port.isdigit():
        return None
    return host


def parse_ss_stdout(stdout: str) -> dict[str, Any]:
    per_ip: dict[str, int] = defaultdict(int)
    total = 0
    for line in stdout.strip().splitlines():
        ip = _peer_ip_from_ss_line(line)
        if ip is None:
            continue
        per_ip[ip] += 1
        total += 1
    return {"total": total, "unique_ips": len(per_ip), "per_ip": dict(per_ip)}


def collect_connections(port: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ss", "-Htn", "state", "established", "sport", "=", f":{port}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return parse_ss_stdout(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return {"total": 0, "unique_ips": 0, "per_ip": {}}


def parse_iptables_monitor_stdout(stdout: str, port: int) -> tuple[int, int]:
    current_in = 0
    current_out = 0
    lines = stdout.strip().splitlines()
    for line in lines[2:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            bytes_count = int(parts[1])
        except ValueError:
            continue
        if f"dpt:{port}" in line:
            current_in += bytes_count
        elif f"spt:{port}" in line:
            current_out += bytes_count
    return current_in, current_out


def collect_iptables_bytes(port: int) -> tuple[int, int]:
    try:
        result = subprocess.run(
            ["iptables", "-L", "MTPROXY_MONITOR", "-n", "-v", "-x"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return 0, 0
        return parse_iptables_monitor_stdout(result.stdout, port)
    except OSError:
        return 0, 0


def check_mtproxy_alive() -> bool:
    try:
        result = subprocess.run(["pgrep", "mtproto-proxy"], capture_output=True, check=False)
        return result.returncode == 0
    except OSError:
        return False


def collect_conntrack() -> dict[str, float | int] | None:
    try:
        count = int(Path("/proc/sys/net/netfilter/nf_conntrack_count").read_text(encoding="utf-8").strip())
        maxv = int(Path("/proc/sys/net/netfilter/nf_conntrack_max").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if maxv <= 0:
        return None
    return {"count": count, "max": maxv, "percent": 100.0 * count / maxv}
