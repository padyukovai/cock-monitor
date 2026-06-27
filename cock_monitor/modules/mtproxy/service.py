"""MTProxy module timer tick."""

from __future__ import annotations

from pathlib import Path


def run_mtproxy_tick(env_file: Path, *, dry_run: bool = False) -> int:
    from cock_monitor.mtproxy_collect_cli import run as mtproxy_run

    args = ["--env-file", str(env_file)]
    if dry_run:
        args.append("--dry-run")
    return mtproxy_run(args)
