#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mtproxy_module.alerts import evaluate_alerts
from mtproxy_module.collector import collect_connections
from mtproxy_module.config import MtproxyConfig
from mtproxy_module.repository import collect_traffic, connect_db, init_schema, store_metric
from cock_monitor.env import merge_env_into_process, parse_env_file
from telegram_bot.telegram_client import TelegramClient


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor mtproxy collect + alerts")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/cock-monitor.env"))
    args = parser.parse_args()

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-mtproxy-collect: env file not found: {env_path}", file=sys.stderr)
        return 1
    raw = parse_env_file(env_path)
    merge_env_into_process(raw)
    cfg = MtproxyConfig.from_env_map(raw)
    if not cfg.enabled:
        return 0

    token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("cock-mtproxy-collect: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 1

    conn = connect_db(cfg.db_path)
    init_schema(conn)
    conns = collect_connections(cfg.mtproxy_port)
    traffic = collect_traffic(conn, cfg.mtproxy_port)
    store_metric(conn, conns, traffic)
    alerts = evaluate_alerts(conn, cfg, conns, traffic)
    client = TelegramClient(token)
    for msg in alerts:
        try:
            client.send_message(chat_id, msg)
        except RuntimeError as e:
            print(f"cock-mtproxy-collect: telegram send failed: {e}", file=sys.stderr)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

