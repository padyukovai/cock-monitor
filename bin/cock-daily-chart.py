#!/usr/bin/env python3
"""Build a PNG chart from cock-monitor SQLite metrics; optionally send via Telegram."""
from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_telegram_client():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from telegram_bot.telegram_client import TelegramClient

    return TelegramClient


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

    # Use Moscow timezone (UTC+3) regardless of server local time.
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
    for i, (lab, col) in enumerate(zip(labels, range(2, 8))):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor daily metrics chart")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/cock-monitor.env"),
        help="Env file with METRICS_DB and optional Telegram vars",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=0,
        help="Window in hours (0 = use DAILY_CHART_HOURS from env or 24)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write PNG to this path",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send chart with caption via Telegram (needs token/chat in env)",
    )
    args = parser.parse_args()

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-daily-chart: env file not found: {env_path}", file=sys.stderr)
        return 1

    raw = _parse_env_file(env_path)
    for k, v in raw.items():
        if k not in os.environ:
            os.environ[k] = v

    db_path = os.environ.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db").strip()
    hours = args.hours
    if hours <= 0:
        h_env = os.environ.get("DAILY_CHART_HOURS", "24").strip()
        try:
            hours = int(h_env)
        except ValueError:
            hours = 24
        if hours <= 0:
            hours = 24

    moscow_tz = timezone(timedelta(hours=3), name="MSK")
    title_suffix = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M MSK")

    out_path = args.output
    if out_path is None:
        out_path = Path(os.environ.get("TMPDIR", "/tmp")) / "cock-monitor-daily.png"

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        print(f"cock-daily-chart: sqlite: {e}", file=sys.stderr)
        return 1

    try:
        rows = fetch_rows(conn, start_ts)
    except sqlite3.OperationalError as e:
        print(
            f"cock-daily-chart: database not ready ({e}); run check-conntrack.sh once to create tables",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()

    try:
        generate_chart(rows, out_path, title_suffix=title_suffix)
    except ImportError as e:
        print(
            "cock-daily-chart: matplotlib required "
            "(e.g. apt install python3-matplotlib)",
            file=sys.stderr,
        )
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"cock-daily-chart: plot failed: {e}", file=sys.stderr)
        return 1

    caption = build_caption(rows, hours)
    if len(caption) > 1024:
        caption = caption[:1021] + "..."

    if args.send_telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            print(
                "cock-daily-chart: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required",
                file=sys.stderr,
            )
            return 1
        TelegramClient = _load_telegram_client()
        client = TelegramClient(token)
        try:
            client.send_photo(chat, out_path, caption=caption)
        except RuntimeError as e:
            print(f"cock-daily-chart: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
