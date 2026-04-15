from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request

from mtproxy_module.config import to_int

_GEO_TTL_SEC = 30 * 24 * 60 * 60


def query_geo_batch(ips: list[str]) -> dict[str, str]:
    if not ips:
        return {}
    req = urllib.request.Request(
        "http://ip-api.com/batch?fields=query,city,isp,status",
        data=json.dumps(ips).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return {ip: "" for ip in ips}
    out: dict[str, str] = {}
    for item in body:
        ip = str(item.get("query", ""))
        if not ip:
            continue
        if item.get("status") == "success":
            out[ip] = f" ({item.get('city', 'Unknown City')}, {item.get('isp', 'Unknown ISP')})"
        else:
            out[ip] = ""
    for ip in ips:
        out.setdefault(ip, "")
    return out


def get_ips_geo_info(conn: sqlite3.Connection, ips: list[str]) -> dict[str, str]:
    if not ips:
        return {}
    now = int(time.time())
    result: dict[str, str] = {}
    pending: list[str] = []
    for ip in sorted(set(ips)):
        row = conn.execute("SELECT data, ts FROM mtproxy_ip_geo_cache WHERE ip = ?", (ip,)).fetchone()
        if row and now - to_int(str(row[1]), 0) <= _GEO_TTL_SEC:
            result[ip] = str(row[0] or "")
        else:
            pending.append(ip)
    for i in range(0, len(pending), 100):
        chunk = pending[i : i + 100]
        chunk_map = query_geo_batch(chunk)
        for ip, geo in chunk_map.items():
            conn.execute(
                "INSERT INTO mtproxy_ip_geo_cache (ip, data, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                (ip, geo, now),
            )
            result[ip] = geo
    conn.commit()
    return result
