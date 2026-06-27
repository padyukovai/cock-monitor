"""Environment checks before deploy or after package updates."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.platform.profile_ops import ProfileOps, load_profile_ops
from cock_monitor.platform.registry import get_registry, module_enabled, parse_enabled_modules


def parse_enabled_modules_safe(env: dict[str, str]) -> list[str]:
    try:
        return parse_enabled_modules(env)
    except ValueError:
        return ["core"]


def _to_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _which(name: str) -> str | None:
    return shutil.which(name)


def _check_tool(name: str, *, required: bool) -> tuple[str, bool]:
    path = _which(name)
    if path:
        return (f"ok: {name} -> {path}", True)
    msg = f"missing: {name} (not on PATH)"
    if required:
        return (f"ERROR: {msg}", False)
    return (f"warn: {msg}", True)


def _check_systemd_unit(unit: str) -> tuple[str, bool]:
    unit_paths = (
        Path("/etc/systemd/system") / unit,
        Path("/lib/systemd/system") / unit,
        Path("/usr/lib/systemd/system") / unit,
    )
    if not any(p.is_file() for p in unit_paths):
        return (f"ERROR: systemd unit not found: {unit}", False)
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = (r.stdout or "").strip() or "unknown"
        if status == "active":
            return (f"ok: systemd {unit} active", True)
        return (f"warn: systemd {unit} installed but status={status}", True)
    except (OSError, subprocess.SubprocessError) as e:
        return (f"warn: cannot check systemd {unit}: {e}", True)


def _check_tcp_port_listen(port: int) -> tuple[str, bool]:
    try:
        r = subprocess.run(
            ["ss", "-ltn", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if f":{port}" in (r.stdout or ""):
            return (f"ok: TCP port {port} listening", True)
        return (f"warn: TCP port {port} not listening", True)
    except (OSError, subprocess.SubprocessError) as e:
        return (f"warn: cannot check TCP port {port}: {e}", True)


def _run_profile_ops_checks(ops: ProfileOps) -> tuple[list[str], bool]:
    lines: list[str] = []
    ok = True
    for unit in ops.preflight_systemd_units:
        text, step_ok = _check_systemd_unit(unit)
        lines.append(text)
        ok = ok and step_ok
    for port in ops.preflight_tcp_ports:
        text, step_ok = _check_tcp_port_listen(port)
        lines.append(text)
        ok = ok and step_ok
    return lines, ok


def run_preflight(
    env_path: Path | None,
    *,
    minimal: bool,
    implicit_env_path: bool = False,
    profile: str | None = None,
) -> int:
    ok = True
    lines: list[str] = []

    for label, req in (
        ("python3", True),
        ("curl", True),
        ("sqlite3", True),
    ):
        text, step_ok = _check_tool(label, required=req)
        lines.append(text)
        ok = ok and step_ok

    text, _ = _check_tool("conntrack", required=False)
    lines.append(text)

    env: dict[str, str] = {}
    if env_path is not None:
        ep = env_path.expanduser().resolve()
        if not ep.is_file():
            if implicit_env_path:
                lines.append(
                    f"warn: env file not found: {ep} (skip env-driven checks)"
                )
            else:
                lines.append(f"ERROR: env file not found: {ep}")
                ok = False
        else:
            try:
                loaded = load_config(ep)
                env = loaded.app.raw
                lines.append(f"ok: loaded env from {ep}")
            except OSError as e:
                lines.append(f"ERROR: cannot read env file {ep}: {e}")
                ok = False

    if ok and env and not minimal:
        bot_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = env.get("TELEGRAM_CHAT_ID", "").strip()
        if bool(bot_token) != bool(chat_id):
            lines.append("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set together")
            ok = False

        enabled = parse_enabled_modules_safe(env)
        lines.append(f"ok: ENABLED_MODULES={','.join(enabled)}")

        registry = get_registry()
        for spec in registry.enabled_specs(env):
            for name in spec.required_tools:
                text, step_ok = _check_tool(name, required=True)
                lines.append(text)
                ok = ok and step_ok

        if module_enabled("vless", env):
            xui = env.get("XUI_DB_PATH", "").strip()
            if xui:
                p = Path(xui).expanduser()
                if not p.is_file():
                    lines.append(f"ERROR: XUI_DB_PATH not a file: {p}")
                    ok = False
                elif not os.access(p, os.R_OK):
                    lines.append(f"ERROR: XUI_DB_PATH not readable: {p}")
                    ok = False
                else:
                    lines.append(f"ok: XUI_DB_PATH readable: {p}")

        burst_access = env.get("BURST_ACCESS_LOG_PATH", "").strip()
        if burst_access:
            p = Path(burst_access).expanduser()
            if not p.is_file():
                lines.append(f"warn: BURST_ACCESS_LOG_PATH not a file: {p}")
            elif not os.access(p, os.R_OK):
                lines.append(f"warn: BURST_ACCESS_LOG_PATH not readable: {p}")
            else:
                lines.append(f"ok: BURST_ACCESS_LOG_PATH readable: {p}")
            text, _ = _check_tool("ss", required=False)
            lines.append(text)
            text, _ = _check_tool("pgrep", required=False)
            lines.append(text)

        want_matplotlib = module_enabled("core", env) or module_enabled("mtproxy", env)
        if want_matplotlib:
            try:
                import matplotlib  # noqa: F401

                lines.append("ok: matplotlib import")
            except ImportError as e:
                lines.append(
                    f"warn: matplotlib not available ({e}); "
                    "install python3-matplotlib for /chart and PNG reports"
                )

    if profile and not minimal:
        try:
            ops = load_profile_ops(profile)
            lines.append(f"ok: profile ops {profile}")
            ops_lines, ops_ok = _run_profile_ops_checks(ops)
            lines.extend(ops_lines)
            ok = ok and ops_ok
        except FileNotFoundError as e:
            lines.append(f"ERROR: {e}")
            ok = False

    for line in lines:
        print(line, file=sys.stdout)

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor environment checks")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=f"Optional env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Only check curl, sqlite3, conntrack warning; skip env-driven checks",
    )
    parser.add_argument(
        "--profile",
        help="Profile name for PREFLIGHT_SYSTEMD_UNITS / PREFLIGHT_TCP_PORTS checks",
    )
    parser.add_argument(
        "env_file_positional",
        nargs="?",
        type=Path,
        default=None,
        help="Alternative to --env-file (same as README one-liner)",
    )
    args = parser.parse_args(argv)

    explicit = args.env_file is not None or args.env_file_positional is not None
    env_path: Path | None = args.env_file
    if args.env_file_positional is not None:
        env_path = args.env_file_positional
    implicit_env_path = False
    if env_path is None and not args.minimal:
        env_path = DEFAULT_ENV_FILE
        implicit_env_path = not explicit

    return run_preflight(
        env_path,
        minimal=args.minimal,
        implicit_env_path=implicit_env_path,
        profile=args.profile,
    )
