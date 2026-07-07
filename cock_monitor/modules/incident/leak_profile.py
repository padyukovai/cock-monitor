"""24h leak investigation profile: enriched samples and auto-report."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cock_monitor.adapters.linux_host import (
    find_process_by_comm,
    parse_ss_state_line_counts,
    read_conntrack_fill,
    read_process_stats,
    read_proc_stat_ticks,
)
from cock_monitor.modules.incident.env import get_int
from cock_monitor.modules.incident.postmortem import send_telegram


def _as_bool(raw: str, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s not in {"0", "false", "False", "no", "NO"}


def leak_investigation_enabled() -> bool:
    return _as_bool(os.environ.get("INCIDENT_LEAK_INVESTIGATION", ""), default=False)


def leak_investigation_hours() -> int:
    return max(1, get_int("INCIDENT_LEAK_INVESTIGATION_HOURS", 24))


def leak_log_path(now_ts: int | None = None) -> Path:
    log_dir = Path(os.environ.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor"))
    ts = now_ts if now_ts is not None else int(time.time())
    return log_dir / f"leak-investigation-{time.strftime('%Y%m%d', time.gmtime(ts))}.jsonl"


def leak_state_path() -> Path:
    return Path(
        os.environ.get(
            "INCIDENT_LEAK_STATE_FILE",
            "/var/lib/cock-monitor/leak_investigation.state",
        )
    )


def load_leak_state() -> dict[str, str]:
    defaults = {
        "active": "0",
        "start_ts": "0",
        "end_ts": "0",
        "report_sent": "0",
        "xray_pid": "0",
        "utime": "0",
        "stime": "0",
        "wall_ns": "0",
    }
    path = leak_state_path()
    if not path.is_file():
        return defaults
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in defaults:
            defaults[k] = v
    return defaults


def save_leak_state(st: dict[str, str]) -> None:
    path = leak_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(f"{k}={st.get(k, '')}" for k in (
        "active", "start_ts", "end_ts", "report_sent",
        "xray_pid", "utime", "stime", "wall_ns",
    )) + "\n"
    tmp = path.parent / f".leak-inv.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def start_leak_investigation(*, hours: int | None = None) -> dict[str, str]:
    now = int(time.time())
    h = hours if hours is not None else leak_investigation_hours()
    st = load_leak_state()
    st["active"] = "1"
    st["start_ts"] = str(now)
    st["end_ts"] = str(now + h * 3600)
    st["report_sent"] = "0"
    save_leak_state(st)
    return st


def stop_leak_investigation() -> dict[str, str]:
    st = load_leak_state()
    st["active"] = "0"
    save_leak_state(st)
    return st


def _conntrack_state_breakdown() -> dict[str, int]:
    if shutil.which("conntrack") is None:
        return {}
    try:
        out = subprocess.run(
            ["conntrack", "-L"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    counts: Counter[str] = Counter()
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        proto = parts[0]
        state = "NA"
        for token in parts:
            if token in (
                "ESTABLISHED", "TIME_WAIT", "CLOSE", "CLOSE_WAIT",
                "SYN_SENT", "SYN_RECV", "FIN_WAIT", "LAST_ACK",
            ):
                state = token
                break
        counts[f"{proto}_{state}"] += 1
    return dict(counts.most_common(12))


def _top_peer_ports(limit: int = 8) -> list[dict[str, Any]]:
    if shutil.which("ss") is None:
        return []
    try:
        out = subprocess.run(
            ["ss", "-tan"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ports: Counter[str] = Counter()
    for line in (out.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        peer = parts[4]
        if ":" not in peer:
            continue
        port = peer.rsplit(":", 1)[-1]
        ports[port] += 1
    return [{"port": p, "count": c} for p, c in ports.most_common(limit)]


@dataclass(frozen=True)
class LeakEnrichedSample:
    xray_rss_mb: float | None
    xray_fds: int | None
    xray_cpu_pct: float | None
    conntrack_states: dict[str, int]
    top_peer_ports: list[dict[str, Any]]


def collect_leak_enriched(st: dict[str, str]) -> LeakEnrichedSample:
    xray_match = os.environ.get("LEAK_XRAY_PROCESS_MATCH", "xray-linux-amd64")
    pid = find_process_by_comm(xray_match) or 0
    prev_pid = int(st.get("xray_pid", "0") or "0")
    prev_ticks = None
    prev_wall = None
    if prev_pid == pid and st.get("utime", "").isdigit() and st.get("stime", "").isdigit():
        prev_ticks = (int(st["utime"]), int(st["stime"]))
        if st.get("wall_ns", "").isdigit():
            prev_wall = int(st["wall_ns"])

    rss = fds = cpu = None
    if pid > 0:
        stats = read_process_stats(pid, prev_ticks=prev_ticks, prev_wall_ns=prev_wall)
        rss = float(stats["rss_mb"]) if stats.get("rss_mb") is not None else None
        fds = int(stats["fds"]) if stats.get("fds") is not None else None
        cpu = float(stats["cpu_pct"]) if stats.get("cpu_pct") is not None else None
        ticks = read_proc_stat_ticks(pid)
        if ticks:
            st["xray_pid"] = str(pid)
            st["utime"] = str(ticks[0])
            st["stime"] = str(ticks[1])
            st["wall_ns"] = str(time.time_ns())
            save_leak_state(st)

    return LeakEnrichedSample(
        xray_rss_mb=rss,
        xray_fds=fds,
        xray_cpu_pct=cpu,
        conntrack_states=_conntrack_state_breakdown(),
        top_peer_ports=_top_peer_ports(),
    )


def build_leak_json_fragment(sample: LeakEnrichedSample) -> dict[str, Any]:
    ct_count, ct_max, ct_fill = read_conntrack_fill()
    return {
        "leak_profile": {
            "version": 1,
            "xray": {
                "rss_mb": sample.xray_rss_mb,
                "fds": sample.xray_fds,
                "cpu_pct": sample.xray_cpu_pct,
            },
            "conntrack": {
                "count": ct_count,
                "max": ct_max,
                "fill_pct": ct_fill,
                "states": sample.conntrack_states,
            },
            "top_peer_ports": sample.top_peer_ports,
        },
    }


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    den_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _load_samples(log_dir: Path, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in ("incident-*.jsonl", "leak-investigation-*.jsonl"):
        for path in sorted(log_dir.glob(pattern)):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = int(row.get("ts_epoch", 0) or 0)
                if start_ts <= ts <= end_ts:
                    rows.append(row)
    return rows


def build_leak_investigation_report(
    *,
    host: str,
    start_ts: int,
    end_ts: int,
    log_dir: Path | None = None,
) -> str:
    log_dir = log_dir or Path(os.environ.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor"))
    samples = _load_samples(log_dir, start_ts, end_ts)
    samples.sort(key=lambda r: int(r.get("ts_epoch", 0) or 0))
    if not samples:
        return f"Leak investigation report ({host})\nNo samples in window."

    rss: list[float] = []
    mem: list[float] = []
    ct: list[float] = []
    tw: list[float] = []
    ts_vals: list[int] = []

    for row in samples:
        ts_vals.append(int(row.get("ts_epoch", 0) or 0))
        mem_kb = row.get("mem_avail_kb")
        if mem_kb is not None:
            mem.append(float(mem_kb))
        leak = row.get("leak_profile") or {}
        xray = leak.get("xray") or {}
        if xray.get("rss_mb") is not None:
            rss.append(float(xray["rss_mb"]))
        elif row.get("xray_rss_mb") is not None:
            rss.append(float(row["xray_rss_mb"]))
        ct_info = leak.get("conntrack") or row.get("conntrack") or {}
        if ct_info.get("fill_pct") is not None:
            ct.append(float(ct_info["fill_pct"]))
        elif ct_info.get("count") is not None:
            ct.append(float(ct_info["count"]))
        tcp = row.get("tcp") or {}
        if tcp.get("time_wait") is not None:
            tw.append(float(tcp["time_wait"]))

    lines = [
        f"<b>Leak investigation report</b> — {host}",
        f"Window: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(start_ts))}"
        f" → {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(end_ts))}",
        f"Samples: {len(samples)}",
    ]

    hypotheses: list[str] = []
    if len(rss) >= 3:
        rss_growth = rss[-1] - rss[0]
        t0 = ts_vals[0]
        xs = [(t - t0) / 3600.0 for t in ts_vals[: len(rss)]]
        corr_rss = _pearson(xs, rss)
        lines.append(f"xray RSS: {rss[0]:.0f} → {rss[-1]:.0f} MB (Δ{rss_growth:+.0f})")
        if rss_growth > 50 and corr_rss > 0.5:
            hypotheses.append("xray memory growth (likely primary leak)")
        elif rss_growth < 20:
            hypotheses.append("xray RSS stable — conntrack/kernel pressure more likely")

    if len(mem) >= 3 and len(rss) >= 3 and len(mem) == len(rss):
        corr_mem = _pearson(rss, [-m for m in mem])
        lines.append(f"RSS↔MemAvailable correlation: r={corr_mem:.2f}")
        if corr_mem > 0.65:
            hypotheses.append("RSS growth correlates with falling MemAvailable")

    if len(ct) >= 3 and len(rss) >= 3:
        n = min(len(ct), len(rss))
        corr_ct = _pearson(rss[-n:], ct[-n:])
        lines.append(f"RSS↔conntrack correlation: r={corr_ct:.2f}")
        if corr_ct > 0.5:
            hypotheses.append("conntrack tracks RSS (symptom or co-leak)")

    if len(tw) >= 3:
        tw_growth = tw[-1] - tw[0]
        lines.append(f"TCP TIME-WAIT: {tw[0]:.0f} → {tw[-1]:.0f} (Δ{tw_growth:+.0f})")
        if tw_growth > 1000:
            hypotheses.append("high TIME-WAIT churn — check local proxy loop / short-lived TCP")

    if not hypotheses:
        hypotheses.append("inconclusive — extend window or enable faster sampling")

    lines.append("")
    lines.append("<b>Hypotheses</b>")
    for h in hypotheses:
        lines.append(f"• {h}")

    return "\n".join(lines)


def maybe_finalize_leak_investigation(host: str) -> None:
    """Send auto-report when 24h window ends."""
    st = load_leak_state()
    if st.get("active") != "1":
        return
    if st.get("report_sent") == "1":
        return
    now = int(time.time())
    end_ts = int(st.get("end_ts", "0") or "0")
    if now < end_ts:
        return
    start_ts = int(st.get("start_ts", "0") or "0")
    if start_ts <= 0:
        return
    if os.environ.get("INCIDENT_LEAK_AUTO_REPORT", "1") != "1":
        st["active"] = "0"
        save_leak_state(st)
        return
    body = build_leak_investigation_report(host=host, start_ts=start_ts, end_ts=now)
    send_telegram(body, parse_mode="HTML")
    st["active"] = "0"
    st["report_sent"] = "1"
    save_leak_state(st)


def append_leak_investigation_line(base_row: dict[str, Any], enriched: LeakEnrichedSample) -> str:
    row = dict(base_row)
    row.update(build_leak_json_fragment(enriched))
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
