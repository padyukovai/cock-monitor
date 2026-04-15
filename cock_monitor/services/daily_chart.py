"""Conntrack metrics PNG chart from METRICS_DB (shared by cock-daily-chart and telegram bot)."""
from __future__ import annotations

import os
import socket
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.env import merge_env_into_process, parse_env_file
from cock_monitor.storage.sqlite_connection import open_sqlite_connection

_CAPTION_MAX = 1024


def fetch_rows(conn: sqlite3.Connection, start_ts: int) -> list[tuple]:
    cur = conn.execute(
        """
        SELECT ts, fill_pct, delta_drop, delta_insert_failed, delta_early_drop,
               delta_error, delta_invalid, delta_search_restart
        FROM conntrack_samples
        WHERE ts >= ?
        ORDER BY ts
        """,
        (start_ts,),
    )
    return list(cur.fetchall())


def generate_chart(
    rows: list[tuple],
    output_path: Path,
    *,
    title_suffix: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")

    if not rows:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "No samples in range", ha="center", va="center")
        ax.set_axis_off()
        plt.tight_layout()
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return

    tz = timezone(timedelta(hours=3), name="MSK")
    ts_list = [datetime.fromtimestamp(r[0], tz=tz) for r in rows]
    fills = [r[1] for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    ax1.plot(ts_list, fills, color="#00e5ff", label="fill %")
    ax1.set_ylabel("fill %")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f"nf_conntrack — {title_suffix}")

    def series(col_idx: int) -> list[float | None]:
        out: list[float | None] = []
        for r in rows:
            v = r[col_idx]
            out.append(float(v) if v is not None else None)
        return out

    colors = ("#ff9800", "#e91e63", "#cddc39", "#f44336", "#9c27b0", "#795548")
    labels = (
        "Δ drop",
        "Δ insert_failed",
        "Δ early_drop",
        "Δ error",
        "Δ invalid",
        "Δ search_restart",
    )
    for i, (lab, col) in enumerate(zip(labels, range(2, 8), strict=True)):
        ys = series(col)
        if any(y is not None for y in ys):
            ax2.plot(
                ts_list,
                [y if y is not None else float("nan") for y in ys],
                color=colors[i],
                label=lab,
                linewidth=1,
            )

    ax2.set_ylabel("delta / interval")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.xticks(rotation=35)

    plt.tight_layout()
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def build_caption(rows: list[tuple], hours: int) -> str:
    host = socket.gethostname()
    if not rows:
        return f"{host}: no samples in last {hours}h"

    fills = [r[1] for r in rows if r[1] is not None]
    mx = max(fills) if fills else 0
    avg = sum(fills) / len(fills) if fills else 0

    def sum_deltas(col: int) -> int:
        s = 0
        for r in rows:
            v = r[col]
            if isinstance(v, int) and v > 0:
                s += v
        return s

    sd = sum_deltas(2)
    sif = sum_deltas(3)
    se = sum_deltas(5)
    return (
        f"{host} — last {hours}h\n"
        f"fill % max/avg: {mx:.0f} / {avg:.1f}\n"
        f"ΣΔ drop={sd} insert_failed={sif} error={se} (positive buckets only)"
    )


def _resolve_hours(hours_arg: int) -> int:
    hours = hours_arg
    if hours <= 0:
        h_env = os.environ.get("DAILY_CHART_HOURS", "24").strip()
        try:
            hours = int(h_env)
        except ValueError:
            hours = 24
        if hours <= 0:
            hours = 24
    return hours


def run_daily_chart(
    env_file: Path,
    output_path: Path,
    *,
    hours: int = 0,
) -> str:
    """
    Load env, read conntrack_samples, write PNG to output_path.
    Returns Telegram-safe caption (max 1024 chars).
    """
    env_path = env_file.expanduser().resolve()
    if not env_path.is_file():
        raise FileNotFoundError(str(env_path))

    raw = parse_env_file(env_path)
    merge_env_into_process(raw)

    db_path = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    hours_win = _resolve_hours(hours)
    start_ts = int(time.time()) - hours_win * 3600

    moscow_tz = timezone(timedelta(hours=3), name="MSK")
    title_suffix = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M MSK")

    try:
        conn = open_sqlite_connection(db_path)
    except sqlite3.Error as e:
        raise RuntimeError(f"sqlite: {e}") from e

    try:
        rows = fetch_rows(conn, start_ts)
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"database not ready ({e}); run check-conntrack.sh once to create tables"
        ) from e
    finally:
        conn.close()

    try:
        generate_chart(rows, output_path, title_suffix=title_suffix)
    except ImportError as e:
        raise ImportError(
            "matplotlib required (e.g. apt install python3-matplotlib)"
        ) from e
    except OSError as e:
        raise RuntimeError(f"plot failed: {e}") from e

    caption = build_caption(rows, hours_win)
    if len(caption) > _CAPTION_MAX:
        caption = caption[: _CAPTION_MAX - 3] + "..."
    return caption
