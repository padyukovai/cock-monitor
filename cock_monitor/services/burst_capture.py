"""On-demand 1 Hz burst capture to JSONL during VPN connection storms."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cock_monitor.adapters.burst_access_log import BurstLogTracker, LogTailState
from cock_monitor.adapters.linux_host import (
    find_process_by_comm,
    parse_ss_port_state_counts,
    parse_ss_summary,
    read_conntrack_fill,
    read_hostname_fqdn,
    read_netstat_tcp_ext,
    read_process_stats,
    read_sockstat_tcp,
)
from cock_monitor.platform.env_runtime import get_int, get_str, load_env_overwrite


def apply_burst_defaults() -> None:
    os.environ.setdefault("BURST_CAPTURE_LOG_DIR", "/var/lib/cock-monitor")
    os.environ.setdefault("BURST_STATE_FILE", "/var/lib/cock-monitor/burst-capture.state")
    os.environ.setdefault("BURST_ACCESS_LOG_PATH", "/var/log/x-ui/3xipl-ap.log")
    os.environ.setdefault("BURST_ERROR_LOG_PATH", "/var/log/x-ui/error.log")
    os.environ.setdefault("BURST_XRAY_PROCESS_MATCH", "xray")
    os.environ.setdefault("BURST_PROBE_PORT", "443")
    os.environ.setdefault("BURST_CLIENT_IP", "")
    os.environ.setdefault("BURST_SAMPLE_INTERVAL_SEC", "1")
    os.environ.setdefault("BURST_MAX_DURATION_SEC", "300")


def state_path() -> Path:
    return Path(get_str("BURST_STATE_FILE", "/var/lib/cock-monitor/burst-capture.state"))


def load_state() -> dict[str, str]:
    out = {"pid": "0", "log_path": "", "started_at": "0"}
    path = state_path()
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k in out:
                out[k] = v
    except OSError:
        pass
    return out


def save_state(pid: int, log_path: Path, started_at: int) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"pid={pid}\nlog_path={log_path}\nstarted_at={started_at}\n"
    tmp = path.parent / f".burst-state.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def clear_state() -> None:
    path = state_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _run_ss_summary() -> dict[str, int]:
    try:
        out = subprocess.run(
            ["ss", "-s"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return parse_ss_summary(out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return {"estab": 0, "orphan": 0, "syn_recv": 0, "timewait": 0}


def _run_ss_port(port: int) -> dict[str, int]:
    try:
        out = subprocess.run(
            ["ss", "-H", "-tan", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return parse_ss_port_state_counts(out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return {"estab": 0, "syn_recv": 0}


def collect_sample(
    *,
    host: str,
    port: int,
    xray_match: str,
    client_ip: str,
    log_tracker: BurstLogTracker,
    xray_pid: int,
    prev_ticks: tuple[int, int] | None,
    prev_wall_ns: int | None,
) -> tuple[dict[str, Any], int, tuple[int, int] | None, int]:
    now_ts = int(time.time())
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    ss = _run_ss_summary()
    sock = read_sockstat_tcp()
    if ss.get("orphan", 0) == 0 and sock.get("orphan", 0):
        ss["orphan"] = sock["orphan"]
    port_counts = _run_ss_port(port)
    ct_count, ct_max, ct_fill = read_conntrack_fill()
    netstat = read_netstat_tcp_ext()
    pid = xray_pid
    if pid <= 0:
        found = find_process_by_comm(xray_match)
        pid = found or 0
    wall_ns = time.time_ns()
    xray_stats = read_process_stats(pid, prev_ticks=prev_ticks, prev_wall_ns=prev_wall_ns)
    access = log_tracker.poll_access(client_ip)
    error = log_tracker.poll_error()
    row: dict[str, Any] = {
        "ts": ts_iso,
        "ts_epoch": now_ts,
        "host": host,
        "sampler": "burst-capture",
        "version": "1",
        "ss": ss,
        "port443": port_counts,
        "conntrack": {"count": ct_count, "max": ct_max, "fill_pct": ct_fill},
        "xray": {
            "pid": pid,
            "rss_mb": xray_stats.get("rss_mb", 0),
            "cpu_pct": xray_stats.get("cpu_pct", 0.0),
            "fds": xray_stats.get("fds", 0),
        },
        "access_log": {
            "delta_lines": access.delta_lines,
            "delta_accepted": access.delta_accepted,
            "delta_from_ip": access.delta_from_ip,
        },
        "error_log": {
            "delta_lines": error.delta_lines,
            "tail": error.tail,
        },
        "netstat": netstat,
    }
    new_ticks = None
    if pid > 0:
        from cock_monitor.adapters.linux_host import read_proc_stat_ticks

        new_ticks = read_proc_stat_ticks(pid)
    return row, pid, new_ticks, wall_ns


def run_capture_loop(log_path: Path, duration_sec: int) -> int:
    apply_burst_defaults()
    host = read_hostname_fqdn()
    port = get_int("BURST_PROBE_PORT", 443)
    interval = max(1, get_int("BURST_SAMPLE_INTERVAL_SEC", 1))
    xray_match = get_str("BURST_XRAY_PROCESS_MATCH", "xray")
    client_ip = get_str("BURST_CLIENT_IP", "")
    access_path = Path(get_str("BURST_ACCESS_LOG_PATH", "/var/log/x-ui/3xipl-ap.log"))
    error_path = Path(get_str("BURST_ERROR_LOG_PATH", "/var/log/x-ui/error.log"))

    tracker = BurstLogTracker()
    if access_path.is_file():
        tracker.access = LogTailState(path=access_path)
    if error_path.is_file():
        tracker.error = LogTailState(path=error_path)
    tracker.seek_all_to_end()

    stop = False

    def _handle_sig(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    deadline = started + duration_sec
    xray_pid = 0
    prev_ticks: tuple[int, int] | None = None
    prev_wall_ns: int | None = None

    with log_path.open("a", encoding="utf-8") as f:
        while not stop and int(time.time()) < deadline:
            t0 = time.monotonic()
            row, xray_pid, new_ticks, wall_ns = collect_sample(
                host=host,
                port=port,
                xray_match=xray_match,
                client_ip=client_ip,
                log_tracker=tracker,
                xray_pid=xray_pid,
                prev_ticks=prev_ticks,
                prev_wall_ns=prev_wall_ns,
            )
            if new_ticks is not None:
                prev_ticks = new_ticks
                prev_wall_ns = wall_ns
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            elapsed = time.monotonic() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0 and not stop and int(time.time()) < deadline:
                time.sleep(sleep_for)

    clear_state()
    return 0


def make_log_path() -> Path:
    log_dir = Path(get_str("BURST_CAPTURE_LOG_DIR", "/var/lib/cock-monitor"))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return log_dir / f"burst-{stamp}.jsonl"


def cmd_start(duration_sec: int) -> int:
    apply_burst_defaults()
    max_dur = get_int("BURST_MAX_DURATION_SEC", 300)
    duration_sec = min(max(1, duration_sec), max_dur)
    st = load_state()
    pid = int(st.get("pid", "0") or "0")
    if pid > 0:
        try:
            os.kill(pid, 0)
            print(f"burst-capture: already running (pid={pid})", file=sys.stderr)
            return 1
        except OSError:
            pass

    log_path = make_log_path()
    started_at = int(time.time())

    child = os.fork()
    if child < 0:
        print("burst-capture: fork failed", file=sys.stderr)
        return 1
    if child == 0:
        os.setsid()
        save_state(os.getpid(), log_path, started_at)
        try:
            raise SystemExit(run_capture_loop(log_path, duration_sec))
        except Exception as e:
            print(f"burst-capture: {e}", file=sys.stderr)
            clear_state()
            raise SystemExit(1) from e

    time.sleep(0.2)
    print(f"burst-capture: started pid={child} log={log_path} duration={duration_sec}s")
    return 0


def cmd_stop() -> int:
    st = load_state()
    pid = int(st.get("pid", "0") or "0")
    log_path = st.get("log_path", "")
    if pid <= 0:
        print("burst-capture: not running", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"burst-capture: stop failed: {e}", file=sys.stderr)
        clear_state()
        return 1
    for _ in range(30):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            break
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if log_path:
        print(f"burst-capture: stopped log={log_path}")
    else:
        print("burst-capture: stopped")
    return 0


def cmd_status() -> int:
    st = load_state()
    pid = int(st.get("pid", "0") or "0")
    if pid <= 0:
        print("burst-capture: inactive")
        return 0
    try:
        os.kill(pid, 0)
    except OSError:
        print("burst-capture: stale state (process gone)")
        return 1
    print(f"burst-capture: running pid={pid} log={st.get('log_path', '')} started_at={st.get('started_at', '')}")
    return 0


def load_env_from_file(env_path: Path) -> None:
    load_env_overwrite(env_path)
    apply_burst_defaults()
