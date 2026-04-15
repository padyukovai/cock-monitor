"""Environment checks before deploy or after package updates."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.env import parse_env_file


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


def run_preflight(
    env_path: Path | None,
    *,
    minimal: bool,
    implicit_env_path: bool = False,
) -> int:
    ok = True
    lines: list[str] = []

    for label, req in (
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
                env = parse_env_file(ep)
                lines.append(f"ok: loaded env from {ep}")
            except OSError as e:
                lines.append(f"ERROR: cannot read env file {ep}: {e}")
                ok = False

    if ok and env and not minimal:
        if _to_bool(env.get("MTPROXY_ENABLE")):
            for name in ("ss", "iptables", "pgrep"):
                text, step_ok = _check_tool(name, required=True)
                lines.append(text)
                ok = ok and step_ok

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

        want_matplotlib = _to_bool(env.get("MTPROXY_ENABLE")) or bool(
            env.get("METRICS_DB", "").strip()
        )
        if want_matplotlib:
            try:
                import matplotlib  # noqa: F401

                lines.append("ok: matplotlib import")
            except ImportError as e:
                lines.append(
                    f"warn: matplotlib not available ({e}); "
                    "install python3-matplotlib for /chart and PNG reports"
                )

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
    )
