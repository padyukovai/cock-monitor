from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from mtproxy_module.collector import check_mtproxy_alive, collect_conntrack
from mtproxy_module.config import MtproxyConfig
from mtproxy_module.formatting import format_bytes
from mtproxy_module.geo import get_ips_geo_info
from mtproxy_module.repository import can_send_alert, load_thresholds


@dataclass(frozen=True)
class AlertCandidate:
    alert_type: str
    alert_key: str
    message: str


def evaluate_alerts(
    conn: sqlite3.Connection,
    cfg: MtproxyConfig,
    conns: dict[str, Any],
    traffic: dict[str, int],
) -> list[AlertCandidate]:
    out: list[AlertCandidate] = []
    warn_per_ip, crit_unique = load_thresholds(conn, cfg)
    total = int(conns.get("total", 0))
    unique_ips = int(conns.get("unique_ips", 0))
    per_ip = dict(conns.get("per_ip", {}))

    if not check_mtproxy_alive():
        if can_send_alert(conn, "down", "global", cfg.alert_cooldown_minutes):
            msg = "MTProxy DOWN\n\nProcess mtproto-proxy was not found."
            out.append(AlertCandidate(alert_type="down", alert_key="global", message=msg))
        return out

    if cfg.conntrack_enabled:
        ct = collect_conntrack()
        if ct is not None:
            pct = float(ct["percent"])
            count = int(ct["count"])
            maxv = int(ct["max"])
            if pct >= cfg.conntrack_crit_fill_percent and can_send_alert(
                conn, "conntrack_critical", "global", cfg.alert_cooldown_minutes
            ):
                msg = f"Conntrack CRITICAL\n\nFill: {pct:.1f}% ({count}/{maxv})"
                out.append(
                    AlertCandidate(
                        alert_type="conntrack_critical",
                        alert_key="global",
                        message=msg,
                    )
                )
            elif pct >= cfg.conntrack_warn_fill_percent and can_send_alert(
                conn, "conntrack_warning", "global", cfg.alert_cooldown_minutes
            ):
                msg = f"Conntrack WARNING\n\nFill: {pct:.1f}% ({count}/{maxv})"
                out.append(
                    AlertCandidate(
                        alert_type="conntrack_warning",
                        alert_key="global",
                        message=msg,
                    )
                )

    geo_map = get_ips_geo_info(conn, list(per_ip.keys()))

    for ip, count in per_ip.items():
        if int(count) <= warn_per_ip:
            continue
        if not can_send_alert(conn, "warning_ip", ip, cfg.alert_cooldown_minutes):
            continue
        msg = (
            f"MTProxy WARNING\n\n"
            f"IP {ip}{geo_map.get(ip, '')} - {count} connections (threshold: {warn_per_ip})\n\n"
            f"Total: {total} conn | {unique_ips} unique IPs\n"
            f"Traffic per interval: Down {format_bytes(traffic['bytes_out'])} | Up {format_bytes(traffic['bytes_in'])}"
        )
        out.append(AlertCandidate(alert_type="warning_ip", alert_key=ip, message=msg))

    if unique_ips > crit_unique and can_send_alert(conn, "critical_leak", "global", cfg.alert_cooldown_minutes):
        sorted_ips = sorted(per_ip.items(), key=lambda x: x[1], reverse=True)[:10]
        top_str = "\n".join(f"  {ip}{geo_map.get(ip, '')} - {count} conn" for ip, count in sorted_ips)
        msg = (
            f"MTProxy LEAK ALERT\n\n"
            f"Detected {unique_ips} unique IPs (threshold: {crit_unique})\n\n"
            f"Top 10:\n{top_str}\n\n"
            f"Traffic per interval: Down {format_bytes(traffic['bytes_out'])} | Up {format_bytes(traffic['bytes_in'])}"
        )
        out.append(AlertCandidate(alert_type="critical_leak", alert_key="global", message=msg))

    return out
