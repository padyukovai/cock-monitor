"""Named deployment roles mapped to profiles and module sets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RolePreset:
    profile: str
    label: str
    modules: tuple[str, ...]


ROLE_PRESETS: dict[str, RolePreset] = {
    "hop-gateway": RolePreset(
        profile="stack-rf3",
        label="RF3 hop-gateway (VLESS tunnel monitor)",
        modules=("core", "hop", "incident", "vless", "entry"),
    ),
    "exit-node": RolePreset(
        profile="stack-exit-node",
        label="Exit node (3x-ui / VLESS + shaper)",
        modules=("core", "vless", "incident", "shaper"),
    ),
    "mtproxy-only": RolePreset(
        profile="stack-mtproxy",
        label="MTProxy-only host (Helsinki)",
        modules=("core", "mtproxy"),
    ),
    "wg-relay": RolePreset(
        profile="stack-rf2-wg",
        label="RF2 WireGuard relay",
        modules=("core", "wg", "incident"),
    ),
    "minimal": RolePreset(
        profile="stack-rf1",
        label="Minimal host monitoring (RF1)",
        modules=("core", "incident"),
    ),
}


def profile_for_role(role: str) -> str:
    preset = ROLE_PRESETS.get(role.strip())
    if preset is None:
        known = ", ".join(sorted(ROLE_PRESETS))
        raise ValueError(f"unknown role {role!r} (known: {known})")
    return preset.profile


def resolve_install_profile(*, role: str | None, profile: str) -> str:
    """Role wins over --profile when both are set."""
    if role:
        return profile_for_role(role)
    return profile


def role_table_lines() -> list[str]:
    lines = ["| Role | Profile | Modules |", "|------|---------|---------|"]
    for role_id, preset in sorted(ROLE_PRESETS.items()):
        mods = ",".join(preset.modules)
        lines.append(f"| `{role_id}` | `{preset.profile}` | `{mods}` |")
    return lines
