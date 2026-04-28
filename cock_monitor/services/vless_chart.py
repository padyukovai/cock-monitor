"""VLESS top downloaders chart generator."""
from __future__ import annotations

from pathlib import Path

from cock_monitor.domain.vless_traffic import fmt_bytes


def _label(email: str, max_len: int = 48) -> str:
    if len(email) <= max_len:
        return email
    return email[: max_len - 1] + "..."


def generate_vless_top_chart(
    rows: list[tuple[str, int]],
    output_path: Path,
    *,
    title: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")

    fig, ax = plt.subplots(figsize=(12, 7))

    if not rows:
        ax.text(0.5, 0.5, "No positive delta in period", ha="center", va="center")
        ax.set_axis_off()
        ax.set_title(title)
        plt.tight_layout()
        fig.savefig(output_path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return

    emails = [_label(email) for email, _ in rows]
    values_gb = [delta / (1024**3) for _, delta in rows]
    indices = list(range(len(rows)))

    bars = ax.barh(indices, values_gb, color="#00c2ff", alpha=0.85)
    ax.set_yticks(indices)
    ax.set_yticklabels(emails, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Delta total (GiB)")
    ax.set_title(title)
    ax.grid(True, axis="x", alpha=0.3)

    max_v = max(values_gb) if values_gb else 0.0
    offset = max_v * 0.01 if max_v > 0 else 0.02
    for bar, (_, delta_bytes) in zip(bars, rows):
        x = bar.get_width()
        y = bar.get_y() + bar.get_height() / 2
        ax.text(
            x + offset,
            y,
            fmt_bytes(delta_bytes),
            va="center",
            ha="left",
            fontsize=8,
            color="#e6f7ff",
        )

    plt.tight_layout()
    fig.savefig(output_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
