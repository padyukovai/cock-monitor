"""Run enabled module timer ticks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.modules.core.service import run_core_tick
from cock_monitor.modules.wg.service import run_wg_collect
from cock_monitor.platform.registry import MODULE_IDS, get_registry, module_enabled


def _run_mtproxy(env_file: Path, *, dry_run: bool) -> int:
    from cock_monitor.mtproxy_collect_cli import run as mtproxy_run

    args = ["--env-file", str(env_file)]
    if dry_run:
        args.append("--dry-run")
    return mtproxy_run(args)


def _run_vless(env_file: Path) -> int:
    from cock_monitor.services.vless_report import run as vless_run

    return vless_run(["--env-file", str(env_file), "--send-telegram", "--mode", "daily"])


def _run_incident(env_file: Path) -> int:
    from cock_monitor.services.incident_sampler import main as incident_main

    return incident_main([str(env_file)])


def _run_shaper(env_file: Path, *, dry_run: bool) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "bin" / "cock-cpu-shaper.sh"
    args = [str(script)]
    if dry_run:
        args.append("--dry-run")
    args.append(str(env_file))
    return subprocess.run(args, check=False).returncode


def _run_core_daily(env_file: Path) -> int:
    import os
    import tempfile

    from cock_monitor.modules.core.charts import run_core_chart
    from cock_monitor.platform.config import load_runtime_env
    from cock_monitor.platform.telegram.client import TelegramClient

    raw = load_runtime_env(env_file)
    token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
    proxy = raw.get("TELEGRAM_PROXY_URL", "").strip() or None
    if not token or not chat_id:
        return 1
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    out = Path(tmp)
    try:
        caption = run_core_chart(env_file, out)
        client = TelegramClient(token, proxy_url=proxy)
        client.send_photo(chat_id, out, caption=caption)
    finally:
        out.unlink(missing_ok=True)
    return 0


_MODULE_RUNNERS = {
    "core": lambda env, dry: run_core_tick(env, dry_run=dry),
    "wg": lambda env, dry: run_wg_collect(env, dry_run=dry),
    "mtproxy": lambda env, dry: _run_mtproxy(env, dry_run=dry),
    "vless": lambda env, dry: _run_vless(env),
    "incident": lambda env, dry: _run_incident(env),
    "shaper": lambda env, dry: _run_shaper(env, dry_run=dry),
}


def run_module(module_id: str, env_file: Path, *, dry_run: bool = False) -> int:
    if module_id not in MODULE_IDS:
        raise ValueError(f"unknown module: {module_id}")
    runner = _MODULE_RUNNERS.get(module_id)
    if runner is None:
        raise ValueError(f"no runner for module: {module_id}")
    from cock_monitor.platform.config import load_runtime_env

    raw = load_runtime_env(env_file)
    if module_id != "core" and not module_enabled(module_id, raw):
        return 0
    if module_id == "core" and not dry_run:
        record = raw.get("METRICS_RECORD_EVERY_RUN", "1").strip() not in {"0", "false", "False"}
        if record or raw.get("ALERT_ON_STATS_DELTA", "0").strip() in {"1", "true"}:
            mgr = __import__(
                "cock_monitor.platform.storage.manager", fromlist=["StorageManager"]
            ).StorageManager(Path(raw.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db")))
            mgr.migrate_all(raw)
    elif module_id != "core":
        mgr = __import__(
            "cock_monitor.platform.storage.manager", fromlist=["StorageManager"]
        ).StorageManager(Path(raw.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db")))
        mgr.migrate_all(raw)
    return runner(env_file, dry_run)


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run a cock-monitor module tick")
    parser.add_argument("module_id", choices=sorted(MODULE_IDS))
    parser.add_argument("env_file", nargs="?", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--daily-chart", action="store_true", help="core only: send daily chart")
    args = parser.parse_args(argv)
    env_file = Path(args.env_file).expanduser().resolve()
    if args.daily_chart:
        if args.module_id != "core":
            parser.error("--daily-chart only applies to core")
        return _run_core_daily(env_file)
    try:
        return run_module(args.module_id, env_file, dry_run=args.dry_run)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2


def list_modules_cmd(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="List cock-monitor modules")
    parser.add_argument("subcmd", nargs="?", choices=["enabled", "all"], default="all")
    parser.add_argument("env_file", nargs="?", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args(argv)
    env_file = Path(args.env_file).expanduser().resolve()
    registry = get_registry()
    if args.subcmd == "enabled" and env_file.is_file():
        from cock_monitor.platform.config import load_runtime_env

        for spec in registry.enabled_specs(load_runtime_env(env_file)):
            print(spec.id)
        return 0
    for spec in registry.all_specs():
        print(f"{spec.id}\t{spec.label}")
    return 0
