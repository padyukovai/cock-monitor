"""Parse Linux host metrics from /proc and lightweight system commands."""
from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path


def parse_loadavg_first_field(loadavg_text: str) -> str | None:
    """First field of /proc/loadavg (1m load), or None if missing."""
    line = loadavg_text.strip().splitlines()
    if not line:
        return None
    parts = line[0].split()
    if not parts:
        return None
    return parts[0] if re.match(r"^[0-9]+(\.[0-9]+)?$", parts[0]) else None


def parse_memavailable_kb(meminfo_text: str) -> int | None:
    for raw in meminfo_text.splitlines():
        parts = raw.split()
        if len(parts) >= 2 and parts[0] == "MemAvailable:":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def read_load_mem_from_proc(
    loadavg_path: Path | None = None,
    meminfo_path: Path | None = None,
) -> tuple[str, int]:
    """Read load1 (string) and MemAvailable kB from /proc or given paths. Defaults: 0, 0."""
    la_p = loadavg_path or Path("/proc/loadavg")
    mi_p = meminfo_path or Path("/proc/meminfo")
    load1 = "0"
    mem_kb = 0
    try:
        t = la_p.read_text(encoding="utf-8", errors="replace")
        v = parse_loadavg_first_field(t)
        if v is not None:
            load1 = v
    except OSError:
        pass
    try:
        t = mi_p.read_text(encoding="utf-8", errors="replace")
        v = parse_memavailable_kb(t)
        if v is not None:
            mem_kb = v
    except OSError:
        pass
    return load1, mem_kb


def parse_ss_tan_state_counts(ss_output: str) -> tuple[int, int, int]:
    """Count ESTAB, SYN-RECV, TIME-WAIT from `ss -tan` stdout (Linux)."""
    counts: dict[str, int] = {}
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 1:
            continue
        state = parts[0]
        if state == "State":
            continue
        counts[state] = counts.get(state, 0) + 1
    estab = counts.get("ESTAB", 0)
    syn = counts.get("SYN-RECV", 0)
    tw = counts.get("TIME-WAIT", 0)
    return estab, syn, tw


def safe_pct(n: int, d: int) -> int:
    if d <= 0:
        return 0
    return (n * 100) // d


def read_hostname_fqdn() -> str:
    """Best-effort host name: FQDN first, then kernel host name."""
    try:
        out = subprocess.run(
            ["hostname", "-f"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        fqdn = (out.stdout or "").strip()
        if fqdn:
            return fqdn
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname() or "unknown-host"


def read_sysctl_int(name: str) -> int | None:
    """Read integer sysctl value via `sysctl -n NAME`, returning None on failure."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    raw = (out.stdout or "").strip()
    if not re.fullmatch(r"[0-9]+", raw):
        return None
    return int(raw)


def read_conntrack_fill() -> tuple[int, int, int]:
    """Return (count, max, fill_pct) for nf_conntrack using sysctl."""
    count = read_sysctl_int("net.netfilter.nf_conntrack_count") or 0
    maxv = read_sysctl_int("net.netfilter.nf_conntrack_max") or 0
    return count, maxv, safe_pct(count, maxv)
