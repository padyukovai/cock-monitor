"""Backward-compatible facade; prefer imports from mtproxy_module submodules."""

from __future__ import annotations

from mtproxy_module.alerts import evaluate_alerts
from mtproxy_module.collector import (
    check_mtproxy_alive,
    collect_conntrack,
    collect_connections,
    collect_iptables_bytes,
    parse_iptables_monitor_stdout,
    parse_ss_stdout,
)
from mtproxy_module.config import MtproxyConfig, to_bool, to_int
from mtproxy_module.formatting import MSK_TZ, format_bytes
from mtproxy_module.geo import get_ips_geo_info, query_geo_batch
from mtproxy_module.repository import (
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
from mtproxy_module.reports import build_period_caption, current_status_text

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
