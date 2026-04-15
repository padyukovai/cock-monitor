"""CLI for MTProxy metrics collection and alerting."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mtproxy_module.alerts import AlertCandidate, evaluate_alerts
from mtproxy_module.collector import collect_connections
from mtproxy_module.config import MtproxyConfig
from mtproxy_module.repository import (
    collect_traffic,
    connect_db,
    init_schema,
    record_alert,
    scenario_transaction,
    store_metric,
)
from telegram_bot.telegram_client import TelegramClient

from cock_monitor.config_loader import load_config
from cock_monitor.env import merge_env_into_process


def dispatch_mtproxy_alerts(
    *,
    conn,
    client: TelegramClient,
    chat_id: str,
    alerts: list[AlertCandidate],
) -> tuple[int, int]:
    sent = 0
    failed = 0
    for alert in alerts:
        result = client.send_message_with_result(chat_id, alert.message)
        if result.success:
            record_alert(conn, alert.alert_type, alert.alert_key, alert.message)
            sent += 1
            print(
                f"cock-mtproxy-collect: alert sent ({alert.alert_type}:{alert.alert_key})",
                file=sys.stderr,
            )
        else:
            failed += 1
            print(
                "cock-mtproxy-collect: alert delivery failed "
                f"({alert.alert_type}:{alert.alert_key}) reason={result.reason}",
                file=sys.stderr,
            )
    return sent, failed


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor mtproxy collect + alerts")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/cock-monitor.env"))
    args = parser.parse_args(argv)

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-mtproxy-collect: env file not found: {env_path}", file=sys.stderr)
        return 1

    loaded = load_config(env_path)
    raw = loaded.app.raw
    merge_env_into_process(raw)
    cfg = MtproxyConfig.from_env_map(raw)
    if not cfg.enabled:
        return 0

    token = loaded.app.telegram.bot_token
    chat_id = loaded.app.telegram.chat_id
    if not token or not chat_id:
        print("cock-mtproxy-collect: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 1

    conn = connect_db(cfg.db_path)
    init_schema(conn)
    conns = collect_connections(cfg.mtproxy_port)
    with scenario_transaction(conn):
        traffic = collect_traffic(conn, cfg.mtproxy_port)
        store_metric(conn, conns, traffic)
        alerts = evaluate_alerts(conn, cfg, conns, traffic)

    client = TelegramClient(token)
    dispatch_mtproxy_alerts(conn=conn, client=client, chat_id=chat_id, alerts=alerts)
    conn.close()
    return 0
