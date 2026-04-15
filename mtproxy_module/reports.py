from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime

from mtproxy_module.collector import check_mtproxy_alive, collect_conntrack, collect_connections
from mtproxy_module.config import MtproxyConfig
from mtproxy_module.formatting import MSK_TZ, format_bytes
from mtproxy_module.geo import get_ips_geo_info
from mtproxy_module.repository import collect_traffic, summary_rows


def current_status_text(conn: sqlite3.Connection, cfg: MtproxyConfig) -> str:
    conns = collect_connections(cfg.mtproxy_port)
    traffic = collect_traffic(conn, cfg.mtproxy_port)
    per_ip = dict(conns.get("per_ip", {}))
    top = sorted(per_ip.items(), key=lambda x: x[1], reverse=True)[:10]
    geo_map = get_ips_geo_info(conn, [ip for ip, _ in top])
    lines = [
        f"MTProxy Status: {'Alive' if check_mtproxy_alive() else 'Down'}",
        "",
        f"Connections: {conns['total']}",
        f"Unique IPs: {conns['unique_ips']}",
        "",
    ]
    if top:
        lines.append("Top 10 IPs:")
        for ip, count in top:
            lines.append(f"  {ip}{geo_map.get(ip, '')} - {count}")
        lines.append("")
    lines.extend(
        [
            "Traffic (delta):",
            f"Down: {format_bytes(traffic['bytes_out'])}",
            f"Up: {format_bytes(traffic['bytes_in'])}",
        ]
    )
    ct = collect_conntrack()
    if ct is not None:
        lines.extend(
            [
                "",
                f"Conntrack: {int(ct['count'])}/{int(ct['max'])} ({float(ct['percent']):.1f}%)",
            ]
        )
    lines.extend(["", f"(sampled: {datetime.now(MSK_TZ).strftime('%H:%M:%S')})"])
    return "\n".join(lines)


def build_period_caption(conn: sqlite3.Connection, start_ts: int, title: str, top_n: int) -> str:
    rows = summary_rows(conn, start_ts)
    if not rows:
        return f"{title}\n\nNo data for selected period."
    max_conn = max(int(r[1]) for r in rows)
    avg_conn = int(sum(int(r[1]) for r in rows) / len(rows))
    max_ips = max(int(r[2]) for r in rows)
    avg_ips = int(sum(int(r[2]) for r in rows) / len(rows))
    sum_in = sum(int(r[3]) for r in rows)
    sum_out = sum(int(r[4]) for r in rows)
    ip_totals: dict[str, int] = defaultdict(int)
    for r in rows:
        raw = r[5]
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        for ip, cnt in dict(parsed).items():
            try:
                ip_totals[str(ip)] += int(cnt)
            except (TypeError, ValueError):
                continue
    top = sorted(ip_totals.items(), key=lambda x: x[1], reverse=True)[:max(1, top_n)]
    geo_map = get_ips_geo_info(conn, [ip for ip, _ in top])
    top_lines = "\n".join(f"  {ip}{geo_map.get(ip, '')} - {cnt} conn" for ip, cnt in top) or "  No data"

    alerts_counts = dict(
        conn.execute(
            "SELECT alert_type, COUNT(*) FROM mtproxy_alerts WHERE ts > ? GROUP BY alert_type",
            (start_ts,),
        ).fetchall()
    )
    return (
        f"{title}\n\n"
        f"Connections (max/avg): {max_conn}/{avg_conn}\n"
        f"Unique IPs (total/max/avg): {len(ip_totals)}/{max_ips}/{avg_ips}\n"
        f"Traffic: Down {format_bytes(sum_out)} | Up {format_bytes(sum_in)}\n\n"
        f"Top {max(1, top_n)} IPs:\n{top_lines}\n\n"
        "Alerts: "
        f"warning_ip={alerts_counts.get('warning_ip', 0)}, "
        f"critical_leak={alerts_counts.get('critical_leak', 0)}, "
        f"down={alerts_counts.get('down', 0)}, "
        f"conntrack_warning={alerts_counts.get('conntrack_warning', 0)}, "
        f"conntrack_critical={alerts_counts.get('conntrack_critical', 0)}"
    )
