"""Lightweight leak-diagnostic probes for core host_samples."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from cock_monitor.adapters.linux_host import (
    find_process_by_comm,
    parse_ss_state_line_counts,
    read_process_stats,
)


@dataclass(frozen=True)
class LeakProbeResult:
    xray_rss_mb: float | None
    xray_fds: int | None
    xray_cpu_pct: float | None
    ss_estab: int | None
    ss_time_wait: int | None
    ss_close_wait: int | None
    ss_fin_wait: int | None


def _read_probe_state(path: Path) -> tuple[int, tuple[int, int] | None, int | None]:
    if not path.is_file():
        return 0, None, None
    pid = 0
    utime = stime = 0
    wall_ns: int | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "xray_pid" and val.isdigit():
            pid = int(val)
        elif key == "utime" and val.isdigit():
            utime = int(val)
        elif key == "stime" and val.isdigit():
            stime = int(val)
        elif key == "wall_ns" and val.isdigit():
            wall_ns = int(val)
    prev_ticks = (utime, stime) if pid else None
    return pid, prev_ticks, wall_ns


def _write_probe_state(path: Path, pid: int, ticks: tuple[int, int], wall_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        f"xray_pid={pid}\n"
        f"utime={ticks[0]}\n"
        f"stime={ticks[1]}\n"
        f"wall_ns={wall_ns}\n"
    )
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".leak_probe.") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _collect_ss_states() -> dict[str, int]:
    if shutil.which("ss") is None:
        return {}
    try:
        out = subprocess.run(
            ["ss", "-tan"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return parse_ss_state_line_counts(out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return {}


def collect_leak_probe(
    *,
    xray_match: str = "xray-linux-amd64",
    state_file: Path | None = None,
) -> LeakProbeResult:
    """Collect xray RSS/FD/CPU and global TCP socket states."""
    state_path = state_file or Path(
        os.environ.get("LEAK_PROBE_STATE_FILE", "/var/lib/cock-monitor/leak_probe.state")
    )
    prev_pid, prev_ticks, prev_wall_ns = _read_probe_state(state_path)
    xray_pid = find_process_by_comm(xray_match) or 0

    xray_rss: float | None = None
    xray_fds: int | None = None
    xray_cpu: float | None = None

    if xray_pid > 0:
        stats = read_process_stats(
            xray_pid,
            prev_ticks=prev_ticks if prev_pid == xray_pid else None,
            prev_wall_ns=prev_wall_ns if prev_pid == xray_pid else None,
        )
        rss = stats.get("rss_mb")
        fds = stats.get("fds")
        cpu = stats.get("cpu_pct")
        xray_rss = float(rss) if isinstance(rss, (int, float)) else None
        xray_fds = int(fds) if isinstance(fds, int) else None
        xray_cpu = float(cpu) if isinstance(cpu, (int, float)) else None

        from cock_monitor.adapters.linux_host import read_proc_stat_ticks

        ticks = read_proc_stat_ticks(xray_pid)
        if ticks:
            _write_probe_state(state_path, xray_pid, ticks, time.time_ns())

    ss = _collect_ss_states()
    return LeakProbeResult(
        xray_rss_mb=xray_rss,
        xray_fds=xray_fds,
        xray_cpu_pct=xray_cpu,
        ss_estab=ss.get("estab"),
        ss_time_wait=ss.get("time_wait"),
        ss_close_wait=ss.get("close_wait"),
        ss_fin_wait=ss.get("fin_wait"),
    )
