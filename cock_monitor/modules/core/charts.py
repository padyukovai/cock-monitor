"""Core daily chart: conntrack + host metrics."""

from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.env import merge_env_into_process
from cock_monitor.platform.storage.manager import StorageManager
from cock_monitor.services.daily_chart import _resolve_hours, build_caption, fetch_rows

_CAPTION_MAX = 1024
MSK_CHART_TZ = timezone(timedelta(hours=3), name="MSK")


def _chart_time_formatter():
    import matplotlib.dates as mdates

    return mdates.DateFormatter("%H:%M", tz=MSK_CHART_TZ)


def _fetch_host_rows(conn, start_ts: int) -> list[tuple]:
    cur = conn.execute(
        """
        SELECT ts, load1, mem_avail_kb
        FROM host_samples
        WHERE ts >= ?
        ORDER BY ts
        """,
        (start_ts,),
    )
    return list(cur.fetchall())


def _fetch_host_leak_rows(conn, start_ts: int) -> list[tuple]:
    cur = conn.execute(
        """
        SELECT ts, mem_avail_kb, xray_rss_mb, xray_fds,
               ss_estab, ss_time_wait, fill_pct
        FROM host_samples h
        LEFT JOIN conntrack_samples c ON c.ts = h.ts
        WHERE h.ts >= ?
        ORDER BY h.ts
        """,
        (start_ts,),
    )
    return list(cur.fetchall())


def generate_leak_chart(
    rows: list[tuple],
    output_path: Path,
    *,
    title_suffix: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    tz = MSK_CHART_TZ
    time_fmt = _chart_time_formatter()

    if not rows:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "No leak samples in range", ha="center", va="center")
        ax.set_axis_off()
        plt.tight_layout()
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    ts_list = [datetime.fromtimestamp(r[0], tz=tz) for r in rows]

    rss = [float(r[2]) if r[2] is not None else float("nan") for r in rows]
    mem_mb = [float(r[1]) / 1024 if r[1] is not None else float("nan") for r in rows]
    axes[0].plot(ts_list, rss, color="#ff5722", label="xray RSS MB")
    ax0b = axes[0].twinx()
    ax0b.plot(ts_list, mem_mb, color="#4caf50", label="MemAvailable MiB", alpha=0.8)
    axes[0].set_ylabel("xray RSS MB")
    ax0b.set_ylabel("MemAvailable MiB")
    axes[0].set_title(f"Memory leak view — {title_suffix}")
    axes[0].grid(True, alpha=0.3)

    fds = [float(r[3]) if r[3] is not None else float("nan") for r in rows]
    axes[1].plot(ts_list, fds, color="#00e5ff", label="xray FDs")
    axes[1].set_ylabel("open FDs")
    axes[1].legend(loc="upper left")
    axes[1].grid(True, alpha=0.3)

    estab = [float(r[4]) if r[4] is not None else float("nan") for r in rows]
    tw = [float(r[5]) if r[5] is not None else float("nan") for r in rows]
    fill = [float(r[6]) if r[6] is not None else float("nan") for r in rows]
    axes[2].plot(ts_list, estab, color="#8bc34a", label="TCP ESTAB")
    axes[2].plot(ts_list, tw, color="#ff9800", label="TCP TIME-WAIT")
    ax2b = axes[2].twinx()
    ax2b.plot(ts_list, fill, color="#e91e63", label="conntrack fill %", alpha=0.7)
    axes[2].set_ylabel("socket states")
    ax2b.set_ylabel("conntrack fill %")
    axes[2].legend(loc="upper left", fontsize=8)
    ax2b.legend(loc="upper right", fontsize=8)
    axes[2].grid(True, alpha=0.3)

    for ax in axes:
        ax.xaxis.set_major_formatter(time_fmt)
        ax.tick_params(axis="x", rotation=35)
    plt.tight_layout()
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def run_leak_chart(env_file: Path, output_path: Path, *, hours: int = 0) -> str:
    env_path = env_file.expanduser().resolve()
    if not env_path.is_file():
        raise FileNotFoundError(str(env_path))

    loaded = load_config(env_path)
    merge_env_into_process(loaded.app.raw)
    db_path = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    hours_win = _resolve_hours(hours)
    start_ts = int(time.time()) - hours_win * 3600

    moscow_tz = MSK_CHART_TZ
    title_suffix = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M MSK")

    mgr = StorageManager(Path(db_path))
    conn = mgr.open()
    try:
        rows = _fetch_host_leak_rows(conn, start_ts)
    finally:
        conn.close()

    generate_leak_chart(rows, output_path, title_suffix=title_suffix)
    caption = f"Leak diagnostics ({hours_win}h)"
    host = socket.gethostname()
    if rows:
        rss_vals = [r[2] for r in rows if r[2] is not None]
        mem_vals = [r[1] for r in rows if r[1] is not None]
        if rss_vals:
            caption += f"\nxray RSS: min={min(rss_vals):.0f} max={max(rss_vals):.0f} MB"
        if mem_vals:
            caption += f"\nMemAvailable min: {min(mem_vals) // 1024} MiB"
    caption = f"{host}\n{caption}"
    if len(caption) > _CAPTION_MAX:
        caption = caption[: _CAPTION_MAX - 3] + "..."
    return caption


def generate_core_chart(
    conntrack_rows: list[tuple],
    host_rows: list[tuple],
    output_path: Path,
    *,
    title_suffix: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    tz = MSK_CHART_TZ
    time_fmt = _chart_time_formatter()

    if not conntrack_rows and not host_rows:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "No samples in range", ha="center", va="center")
        ax.set_axis_off()
        plt.tight_layout()
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=False)

    if conntrack_rows:
        ts_list = [datetime.fromtimestamp(r[0], tz=tz) for r in conntrack_rows]
        fills = [r[1] for r in conntrack_rows]
        axes[0].plot(ts_list, fills, color="#00e5ff", label="fill %")
        axes[0].set_ylabel("fill %")
        axes[0].legend(loc="upper left")
        axes[0].grid(True, alpha=0.3)
        axes[0].set_title(f"nf_conntrack — {title_suffix}")

        colors = ("#ff9800", "#e91e63", "#cddc39")
        labels = ("Δ drop", "Δ insert_failed", "Δ early_drop")
        for i, (lab, col) in enumerate(zip(labels, range(2, 5), strict=True)):
            ys = [float(r[col]) if r[col] is not None else float("nan") for r in conntrack_rows]
            if any(y == y for y in ys):
                axes[1].plot(ts_list, ys, color=colors[i], label=lab, linewidth=1)
        axes[1].set_ylabel("delta / interval")
        axes[1].legend(loc="upper left", fontsize=8)
        axes[1].grid(True, alpha=0.3)
    else:
        axes[0].text(0.5, 0.5, "No conntrack samples", ha="center", va="center")
        axes[1].set_axis_off()

    if host_rows:
        h_ts = [datetime.fromtimestamp(r[0], tz=tz) for r in host_rows]
        load1 = [float(r[1]) if r[1] is not None else float("nan") for r in host_rows]
        mem_mb = [
            float(r[2]) / 1024 if r[2] is not None else float("nan") for r in host_rows
        ]
        axes[2].plot(h_ts, load1, color="#4caf50", label="load1")
        ax2 = axes[2].twinx()
        ax2.plot(h_ts, mem_mb, color="#ff5722", label="MemAvailable MiB", alpha=0.8)
        axes[2].set_ylabel("load1")
        ax2.set_ylabel("MemAvailable MiB")
        axes[2].grid(True, alpha=0.3)
        axes[2].set_title("Host metrics")
        axes[2].xaxis.set_major_formatter(time_fmt)
        axes[2].tick_params(axis="x", rotation=35)
    else:
        axes[2].text(0.5, 0.5, "No host samples", ha="center", va="center")

    if conntrack_rows:
        for ax in (axes[0], axes[1]):
            ax.xaxis.set_major_formatter(time_fmt)
            ax.tick_params(axis="x", rotation=35)
    plt.tight_layout()
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def run_core_chart(env_file: Path, output_path: Path, *, hours: int = 0) -> str:
    env_path = env_file.expanduser().resolve()
    if not env_path.is_file():
        raise FileNotFoundError(str(env_path))

    loaded = load_config(env_path)
    merge_env_into_process(loaded.app.raw)
    db_path = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    hours_win = _resolve_hours(hours)
    start_ts = int(time.time()) - hours_win * 3600

    moscow_tz = MSK_CHART_TZ
    title_suffix = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M MSK")

    mgr = StorageManager(Path(db_path))
    conn = mgr.open()
    try:
        ct_rows = fetch_rows(conn, start_ts)
        host_rows = _fetch_host_rows(conn, start_ts)
    finally:
        conn.close()

    generate_core_chart(ct_rows, host_rows, output_path, title_suffix=title_suffix)
    caption = build_caption(ct_rows, hours_win)
    host = socket.gethostname()
    if host_rows:
        mems = [r[2] for r in host_rows if r[2] is not None]
        if mems:
            caption += f"\nMemAvailable min: {min(mems) // 1024} MiB"
    caption = f"{host}\n{caption}"
    if len(caption) > _CAPTION_MAX:
        caption = caption[: _CAPTION_MAX - 3] + "..."
    return caption
