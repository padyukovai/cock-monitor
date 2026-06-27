"""Parse Linux host metrics from /proc and lightweight system commands."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
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


def parse_ss_state_line_counts(ss_output: str) -> dict[str, int]:
    """Count TCP socket states from `ss -tan` / `ss -Htan` line-oriented output."""
    counts: dict[str, int] = {}
    for line in ss_output.splitlines():
        parts = line.split()
        if not parts:
            continue
        state = parts[0]
        if state == "State":
            continue
        counts[state] = counts.get(state, 0) + 1
    fin_wait = counts.get("FIN-WAIT-1", 0) + counts.get("FIN-WAIT-2", 0)
    return {
        "estab": counts.get("ESTAB", 0),
        "syn_recv": counts.get("SYN-RECV", 0),
        "time_wait": counts.get("TIME-WAIT", 0),
        "fin_wait": fin_wait,
        "close_wait": counts.get("CLOSE-WAIT", 0),
    }


def parse_ss_tan_state_counts(ss_output: str) -> tuple[int, int, int]:
    """Count ESTAB, SYN-RECV, TIME-WAIT from `ss -tan` stdout (Linux)."""
    states = parse_ss_state_line_counts(ss_output)
    return states["estab"], states["syn_recv"], states["time_wait"]


def parse_ss_tan_extended_counts(ss_output: str) -> dict[str, int]:
    """Extended TCP state counts from `ss -tan` stdout."""
    return parse_ss_state_line_counts(ss_output)


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


def sockstat_field(sockstat_text: str, label: str, key: str) -> int | None:
    """Read one counter from /proc/net/sockstat (e.g. TCP: inuse)."""
    for line in sockstat_text.splitlines():
        parts = line.split()
        if not parts or parts[0] != label:
            continue
        for i in range(1, len(parts) - 1, 2):
            if parts[i] == key:
                try:
                    return int(parts[i + 1])
                except ValueError:
                    return None
    return None


def read_sockstat_tcp(sockstat_path: Path | None = None) -> dict[str, int]:
    """Return TCP inuse/orphan/tw from /proc/net/sockstat."""
    path = sockstat_path or Path("/proc/net/sockstat")
    out: dict[str, int] = {"inuse": 0, "orphan": 0, "tw": 0}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for key in out:
        val = sockstat_field(text, "TCP:", key)
        if val is not None:
            out[key] = val
    return out


def parse_ss_summary(ss_output: str) -> dict[str, int]:
    """Parse `ss -s` summary counters: estab, orphan, syn_recv, timewait."""
    out = {"estab": 0, "orphan": 0, "syn_recv": 0, "timewait": 0}
    for line in ss_output.splitlines():
        if not line.startswith("TCP:"):
            continue
        for key, field in (
            ("estab", "estab"),
            ("orphan", "orphaned"),
            ("syn_recv", "synrecv"),
            ("timewait", "timewait"),
        ):
            m = re.search(rf"\b{field}\s+(\d+)", line, re.IGNORECASE)
            if m:
                out[key] = int(m.group(1))
        break
    return out


def parse_ss_port_state_counts(ss_output: str) -> dict[str, int]:
    """Count TCP states from filtered `ss -Htan` output."""
    states = parse_ss_state_line_counts(ss_output)
    return {
        "estab": states["estab"],
        "syn_recv": states["syn_recv"],
        "time_wait": states["time_wait"],
        "fin_wait": states["fin_wait"],
        "close_wait": states["close_wait"],
    }


def parse_netstat_tcp_ext(netstat_text: str, keys: tuple[str, ...]) -> dict[str, int]:
    """Parse selected TcpExt counters from /proc/net/netstat."""
    header: list[str] = []
    values: list[str] = []
    out = {k: 0 for k in keys}
    for line in netstat_text.splitlines():
        if line.startswith("TcpExt:"):
            if not header:
                header = line.split()[1:]
            else:
                values = line.split()[1:]
                break
    if not header or not values:
        return out
    idx_map = {name: i for i, name in enumerate(header)}
    for key in keys:
        i = idx_map.get(key)
        if i is not None and i < len(values):
            try:
                out[key] = int(values[i])
            except ValueError:
                pass
    return out


def read_netstat_tcp_ext(
    netstat_path: Path | None = None,
    keys: tuple[str, ...] = ("ListenOverflows", "TCPTimeouts"),
) -> dict[str, int]:
    """Read TcpExt counters from /proc/net/netstat."""
    path = netstat_path or Path("/proc/net/netstat")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {k: 0 for k in keys}
    return parse_netstat_tcp_ext(text, keys)


def find_process_by_comm(pattern: str) -> int | None:
    """Return newest PID matching comm pattern via pgrep -n."""
    if not pattern.strip():
        return None
    try:
        out = subprocess.run(
            ["pgrep", "-n", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    raw = (out.stdout or "").strip().splitlines()
    if not raw:
        return None
    try:
        return int(raw[0])
    except ValueError:
        return None


def read_proc_stat_ticks(pid: int) -> tuple[int, int] | None:
    """Return (utime, stime) jiffies from /proc/pid/stat."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close = text.rfind(")")
    if close < 0:
        return None
    rest = text[close + 2 :].split()
    if len(rest) < 12:
        return None
    try:
        return int(rest[11]), int(rest[12])
    except ValueError:
        return None


def read_process_stats(
    pid: int,
    *,
    prev_ticks: tuple[int, int] | None = None,
    prev_wall_ns: int | None = None,
    clock_ticks: int | None = None,
) -> dict[str, float | int | None]:
    """RSS (MB), CPU%, open FD count; cpu_pct needs prev tick sample."""
    out: dict[str, float | int | None] = {"rss_mb": 0, "cpu_pct": 0.0, "fds": 0}
    if pid <= 0:
        return out
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    out["rss_mb"] = round(int(parts[1]) / 1024, 1)
                break
    except OSError:
        pass
    try:
        out["fds"] = sum(1 for _ in Path(f"/proc/{pid}/fd").iterdir())
    except OSError:
        out["fds"] = 0
    ticks = read_proc_stat_ticks(pid)
    if ticks and prev_ticks and prev_wall_ns:
        ct = clock_ticks or (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)
        wall = time.time_ns() - prev_wall_ns
        if wall > 0 and ct > 0:
            delta_jiffies = (ticks[0] - prev_ticks[0]) + (ticks[1] - prev_ticks[1])
            out["cpu_pct"] = round((delta_jiffies / ct) * 1e9 / wall * 100, 1)
    return out
