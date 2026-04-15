"""Adapter that owns report text formatting for VLESS delivery channels."""
from __future__ import annotations

from cock_monitor.domain.vless_traffic import build_report, top_downloaders_by_delta_total


def format_vless_report(
    *,
    host: str,
    title: str,
    subtitle: str,
    current_map: dict[str, int],
    prev_map: dict[str, int],
    top_n: int,
    abuse_gb: float,
    abuse_share_pct: float,
    min_total_mb: int,
    ip_counts: dict[str, tuple[int, int]] | None,
    ip_top_k: int,
    ip_truncated: bool,
) -> tuple[str, int, int, str, int]:
    return build_report(
        host=host,
        title=title,
        subtitle=subtitle,
        current_map=current_map,
        prev_map=prev_map,
        top_n=top_n,
        abuse_gb=abuse_gb,
        abuse_share_pct=abuse_share_pct,
        min_total_mb=min_total_mb,
        ip_counts=ip_counts,
        ip_top_k=ip_top_k,
        ip_truncated=ip_truncated,
    )


def build_vless_top_downloaders(
    *,
    current_map: dict[str, int],
    prev_map: dict[str, int],
    top_n: int,
) -> list[tuple[str, int]]:
    return top_downloaders_by_delta_total(current_map, prev_map, top_n=top_n)
