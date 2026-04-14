from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


MSK_TZ = timezone(timedelta(hours=3), name="MSK")


def generate_mtproxy_chart(rows: list[tuple], output_path: Path, *, title: str) -> None:
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

    ts = [datetime.fromtimestamp(int(r[0]), tz=MSK_TZ) for r in rows]
    conns = [int(r[1]) for r in rows]
    ips = [int(r[2]) for r in rows]
    bytes_in = [int(r[3]) / (1024 * 1024) for r in rows]
    bytes_out = [int(r[4]) / (1024 * 1024) for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    ax1.plot(ts, conns, color="#00e5ff", label="Connections")
    ax1.set_ylabel("Connections")
    ax1_ips = ax1.twinx()
    ax1_ips.plot(ts, ips, color="#ff00ff", linestyle="dashed", label="Unique IPs")
    ax1_ips.set_ylabel("Unique IPs")
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax1_ips.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left")
    ax1.set_title(title)
    ax1.grid(True, alpha=0.3)

    width = 0.003
    ax2.bar(ts, bytes_out, width=width, label="Down (MB)", color="#00ff00", alpha=0.7)
    ax2.bar(ts, bytes_in, width=width, bottom=bytes_out, label="Up (MB)", color="#ffaa00", alpha=0.7)
    ax2.set_ylabel("Traffic (MB / interval)")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.xticks(rotation=35)
    plt.tight_layout()
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)

