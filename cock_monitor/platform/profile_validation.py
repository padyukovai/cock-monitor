"""Profile / role consistency checks beyond schema validation."""

from __future__ import annotations

from cock_monitor.platform.registry import module_enabled, parse_enabled_modules
from cock_monitor.platform.roles import ROLE_PRESETS


def validate_profile_env(
    env: dict[str, str],
    *,
    profile: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for module/key expectations."""
    errors: list[str] = []
    warnings: list[str] = []
    enabled = set(parse_enabled_modules(env))

    if module_enabled("hop", env) and not env.get("HOP_LINKS", "").strip():
        errors.append("hop module enabled but HOP_LINKS is empty")

    preset = None
    if profile:
        for role_preset in ROLE_PRESETS.values():
            if role_preset.profile == profile:
                preset = role_preset
                break

    if preset:
        expected = set(preset.modules)
        missing = expected - enabled - {"core"}
        extra = enabled - expected
        if missing:
            warnings.append(
                f"profile {profile} expects modules {sorted(expected)}; missing: {sorted(missing)}"
            )
        noisy = extra - {"core"}
        if noisy and profile == "stack-mtproxy":
            warnings.append(
                f"lean mtproxy profile should not enable extra modules: {sorted(noisy)}"
            )

    if profile in {"stack-rf3", "stack-exit-node", "stack-3xui"}:
        if profile == "stack-rf3" and "hop" not in enabled:
            warnings.append("stack-rf3 expects hop module in ENABLED_MODULES")
        if profile in {"stack-exit-node", "stack-3xui"}:
            if "vless" not in enabled:
                warnings.append("exit-node profile expects vless module")
            if "shaper" not in enabled:
                warnings.append("exit-node profile expects shaper module")

    return errors, warnings
