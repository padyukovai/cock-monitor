"""Run enabled module timer ticks."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.platform.registry import MODULE_IDS, get_registry, module_enabled


def _run_core_daily(env_file: Path) -> int:
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


def _migrate_before_tick(module_id: str, raw: dict[str, str], *, dry_run: bool) -> None:
    from cock_monitor.platform.storage.manager import StorageManager

    db = Path(raw.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db"))
    mgr = StorageManager(db)
    if module_id == "core" and not dry_run:
        record = raw.get("METRICS_RECORD_EVERY_RUN", "1").strip() not in {"0", "false", "False"}
        if record or raw.get("ALERT_ON_STATS_DELTA", "0").strip() in {"1", "true"}:
            mgr.migrate_all(raw)
    elif module_id != "core":
        mgr.migrate_all(raw)


def run_module(module_id: str, env_file: Path, *, dry_run: bool = False) -> int:
    if module_id not in MODULE_IDS:
        raise ValueError(f"unknown module: {module_id}")
    from cock_monitor.platform.config import load_runtime_env

    raw = load_runtime_env(env_file)
    if module_id != "core" and not module_enabled(module_id, raw):
        return 0
    registry = get_registry()
    _migrate_before_tick(module_id, raw, dry_run=dry_run)
    return registry.run_tick_for(module_id, env_file, dry_run=dry_run)


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
