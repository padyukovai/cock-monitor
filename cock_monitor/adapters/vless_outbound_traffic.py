"""Collect outbound hop traffic counters for VLESS reports."""
from __future__ import annotations

import sqlite3

from cock_monitor.adapters.hop_links import parse_hop_links_env, resolve_hop_links_raw
from cock_monitor.adapters.xray_stats import query_outbound_traffic_stats
from cock_monitor.adapters.xui_sqlite import (
    OutboundTrafficRow,
    fetch_outbound_traffics,
    fetch_xray_outbound_tags_from_config,
)
from cock_monitor.domain.vless_traffic import is_hop_outbound_tag

DEFAULT_XRAY_API_ADDR = "127.0.0.1:62789"
DEFAULT_XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
DEFAULT_XRAY_CONFIG_PATH = "/usr/local/x-ui/bin/config.json"


def resolve_hop_tags(env: dict[str, str], *, config_path: str) -> set[str]:
    links = parse_hop_links_env(resolve_hop_links_raw(env))
    if links:
        return {str(link["name"]).strip().lower() for link in links if link.get("name")}
    return {tag.lower() for tag in fetch_xray_outbound_tags_from_config(config_path)}


def collect_outbound_traffic_rows(
    xui_conn: sqlite3.Connection,
    *,
    env: dict[str, str],
) -> tuple[list[OutboundTrafficRow], set[str], str]:
    hop_tags = resolve_hop_tags(
        env,
        config_path=env.get("VLESS_XRAY_CONFIG_PATH", DEFAULT_XRAY_CONFIG_PATH).strip()
        or DEFAULT_XRAY_CONFIG_PATH,
    )
    if not hop_tags:
        return [], set(), ""

    db_rows = fetch_outbound_traffics(xui_conn)
    filtered_db = [row for row in db_rows if is_hop_outbound_tag(row.tag, hop_tags=hop_tags)]
    if filtered_db:
        return filtered_db, hop_tags, "db"

    api_addr = env.get("VLESS_XRAY_API_ADDR", DEFAULT_XRAY_API_ADDR).strip() or DEFAULT_XRAY_API_ADDR
    xray_bin = env.get("VLESS_XRAY_BIN", DEFAULT_XRAY_BIN).strip() or DEFAULT_XRAY_BIN
    result = query_outbound_traffic_stats(api_addr=api_addr, xray_bin=xray_bin)
    if result.error:
        return [], hop_tags, result.error

    filtered_api = [row for row in result.rows if is_hop_outbound_tag(row.tag, hop_tags=hop_tags)]
    if filtered_api:
        return filtered_api, hop_tags, "xray_api"
    return [], hop_tags, "no_outbound_stats"


def outbound_rows_to_maps(
    rows: list[OutboundTrafficRow],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    up_map: dict[str, int] = {}
    down_map: dict[str, int] = {}
    total_map: dict[str, int] = {}
    for row in rows:
        up_map[row.tag] = row.up
        down_map[row.tag] = row.down
        total_map[row.tag] = row.total
    return up_map, down_map, total_map
