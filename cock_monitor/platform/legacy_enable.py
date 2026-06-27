"""Resolve module enablement: ENABLED_MODULES is canonical; legacy flags are deprecated."""

from __future__ import annotations

import sys
import warnings

from cock_monitor.platform.registry import module_enabled

LEGACY_ENABLE_FLAGS: dict[str, str] = {
    "shaper": "SHAPER_ENABLE",
    "incident": "INCIDENT_SAMPLER_ENABLE",
    "mtproxy": "MTPROXY_ENABLE",
}


def _is_true(raw: str | None) -> bool:
    return (raw or "").strip() in {"1", "true", "True", "yes", "on"}


def _warn_deprecated(legacy_key: str, module_id: str) -> None:
    msg = (
        f"{legacy_key} is deprecated; add {module_id!r} to ENABLED_MODULES "
        f"(e.g. ENABLED_MODULES=core,{module_id})"
    )
    warnings.warn(msg, DeprecationWarning, stacklevel=3)
    print(f"warn: {msg}", file=sys.stderr)


def resolve_module_enabled(module_id: str, env: dict[str, str]) -> bool:
    """True if module is enabled via ENABLED_MODULES or a deprecated legacy flag."""
    if module_enabled(module_id, env):
        return True
    legacy_key = LEGACY_ENABLE_FLAGS.get(module_id)
    if legacy_key and _is_true(env.get(legacy_key)):
        _warn_deprecated(legacy_key, module_id)
        return True
    return False
