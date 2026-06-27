"""Profile ops metadata: post-install scripts and preflight requirements."""

from __future__ import annotations

from dataclasses import dataclass

from cock_monitor.platform.config import PROFILE_OPS_KEYS, load_profile


def split_ops_list(raw: str) -> list[str]:
    """Split comma/space-separated profile ops values."""
    items: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        for part in chunk.split():
            value = part.strip()
            if value:
                items.append(value)
    return items


@dataclass(frozen=True)
class ProfileOps:
    post_install_scripts: tuple[str, ...]
    preflight_systemd_units: tuple[str, ...]
    preflight_tcp_ports: tuple[int, ...]


def load_profile_ops(profile_name: str) -> ProfileOps:
    profile = load_profile(profile_name)
    scripts = tuple(split_ops_list(profile.get("POST_INSTALL_SCRIPTS", "")))
    units = tuple(split_ops_list(profile.get("PREFLIGHT_SYSTEMD_UNITS", "")))
    ports: list[int] = []
    for raw in split_ops_list(profile.get("PREFLIGHT_TCP_PORTS", "")):
        try:
            ports.append(int(raw))
        except ValueError:
            continue
    return ProfileOps(
        post_install_scripts=scripts,
        preflight_systemd_units=units,
        preflight_tcp_ports=tuple(ports),
    )


def format_post_install_checklist(profile_name: str) -> list[str]:
    """Human-readable post-install steps for a profile (empty if none)."""
    try:
        ops = load_profile_ops(profile_name)
    except FileNotFoundError:
        return []
    if not ops.post_install_scripts:
        return []
    lines = ["Post-install steps (manual — not run automatically):"]
    for script in ops.post_install_scripts:
        lines.append(f"  sudo bash {script}")
    lines.append("  Re-run with --run-post-install to execute these scripts.")
    return lines
