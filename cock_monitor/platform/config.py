"""Config v2: ENABLED_MODULES, profile/fragment merge."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.env import parse_env_file
from cock_monitor.platform.registry import parse_enabled_modules

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRAGMENTS_DIR = _REPO_ROOT / "config" / "fragments"
_PROFILES_DIR = _REPO_ROOT / "config" / "profiles"


def repo_root() -> Path:
    return _REPO_ROOT


def fragments_dir() -> Path:
    return _FRAGMENTS_DIR


def profiles_dir() -> Path:
    return _PROFILES_DIR


def _parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("'\"")
    return out


def load_fragment(module_id: str) -> dict[str, str]:
    path = _FRAGMENTS_DIR / f"{module_id}.env"
    if not path.is_file():
        raise FileNotFoundError(f"config fragment not found: {path}")
    return _parse_env_text(path.read_text(encoding="utf-8"))


def load_profile(profile_name: str) -> dict[str, str]:
    path = _PROFILES_DIR / f"{profile_name}.env"
    if not path.is_file():
        raise FileNotFoundError(f"profile not found: {path}")
    return _parse_env_text(path.read_text(encoding="utf-8"))


def build_env_from_profile(
    profile_name: str,
    *,
    modules_override: list[str] | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge core fragment + enabled module fragments + profile overrides."""
    profile = load_profile(profile_name)
    enabled = modules_override or parse_enabled_modules(profile)
    merged: dict[str, str] = {}
    for mid in enabled:
        try:
            frag = load_fragment(mid)
        except FileNotFoundError:
            if mid == "core":
                raise
            continue
        merged.update(frag)
    merged.update(profile)
    if modules_override:
        merged["ENABLED_MODULES"] = ",".join(enabled)
    if overrides:
        merged.update(overrides)
    if "ENABLED_MODULES" not in merged:
        merged["ENABLED_MODULES"] = ",".join(enabled)
    return merged


def write_env_file(path: Path, env: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in sorted(env.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def load_runtime_env(path: Path) -> dict[str, str]:
    return parse_env_file(path.expanduser().resolve())
