"""Backward-compatible facade; prefer imports from cock_monitor.modules.mtproxy submodules."""

from __future__ import annotations

from cock_monitor.modules.mtproxy.alerts import evaluate_alerts
from cock_monitor.modules.mtproxy.collector import (
    check_mtproxy_alive,
    collect_connections,
    collect_conntrack,
    collect_iptables_bytes,
    parse_iptables_monitor_stdout,
    parse_ss_stdout,
)
from cock_monitor.modules.mtproxy.config import MtproxyConfig, to_bool, to_int
from cock_monitor.modules.mtproxy.formatting import MSK_TZ, format_bytes
from cock_monitor.modules.mtproxy.geo import get_ips_geo_info, query_geo_batch
from cock_monitor.modules.mtproxy.reports import build_period_caption, current_status_text
from cock_monitor.modules.mtproxy.repository import (
    can_send_alert,
    collect_traffic,
    connect_db,
    init_schema,
    load_thresholds,
    record_alert,
    store_metric,
    summary_rows,
    update_threshold,
)

__all__ = [
    "MSK_TZ",
    "MtproxyConfig",
    "build_period_caption",
    "can_send_alert",
    "check_mtproxy_alive",
    "collect_conntrack",
    "collect_connections",
    "collect_iptables_bytes",
    "collect_traffic",
    "connect_db",
    "current_status_text",
    "evaluate_alerts",
    "format_bytes",
    "get_ips_geo_info",
    "init_schema",
    "load_thresholds",
    "parse_iptables_monitor_stdout",
    "parse_ss_stdout",
    "query_geo_batch",
    "record_alert",
    "store_metric",
    "summary_rows",
    "to_bool",
    "to_int",
    "update_threshold",
]
