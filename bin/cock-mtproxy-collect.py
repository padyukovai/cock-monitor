#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mtproxy_module.core import (
    MtproxyConfig,
    collect_connections,
    collect_traffic,
    connect_db,
    evaluate_alerts,
    init_schema,
    store_metric,
)
from telegram_bot.telegram_client import TelegramClient


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor mtproxy collect + alerts")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/cock-monitor.env"))
    args = parser.parse_args()

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-mtproxy-collect: env file not found: {env_path}", file=sys.stderr)
        return 1
    raw = _parse_env_file(env_path)
    for k, v in raw.items():
        if k not in os.environ:
            os.environ[k] = v
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

