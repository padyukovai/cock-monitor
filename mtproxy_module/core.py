from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MSK_TZ = timezone(timedelta(hours=3), name="MSK")
_GEO_TTL_SEC = 30 * 24 * 60 * 60


def _to_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _to_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def format_bytes(n: int) -> str:
    val = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            if unit == "B":
                return f"{int(val)} {unit}"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"


@dataclass(frozen=True)
class MtproxyConfig:
    enabled: bool
    db_path: Path
    mtproxy_port: int
    max_connections_per_ip: int
    max_unique_ips: int
    alert_cooldown_minutes: int
    daily_report_top_n: int
    conntrack_enabled: bool
    conntrack_warn_fill_percent: int
    conntrack_crit_fill_percent: int

    @classmethod
    def from_env_map(cls, env: dict[str, str]) -> MtproxyConfig:
        db = env.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db").strip()
        return cls(
            enabled=_to_bool(env.get("MTPROXY_ENABLE"), False),
            db_path=Path(db).expanduser(),
            mtproxy_port=_to_int(env.get("MTPROXY_PORT"), 8443),
            max_connections_per_ip=_to_int(env.get("MTPROXY_MAX_CONNECTIONS_PER_IP"), 20),
            max_unique_ips=_to_int(env.get("MTPROXY_MAX_UNIQUE_IPS"), 50),
            alert_cooldown_minutes=_to_int(env.get("MTPROXY_ALERT_COOLDOWN_MINUTES"), 30),
            daily_report_top_n=_to_int(env.get("MTPROXY_DAILY_TOP_N"), 10),
            conntrack_enabled=_to_bool(env.get("MTPROXY_CONNTRACK_ENABLE"), False),
            conntrack_warn_fill_percent=_to_int(env.get("MTPROXY_CONNTRACK_WARN_FILL_PERCENT"), 80),
            conntrack_crit_fill_percent=_to_int(env.get("MTPROXY_CONNTRACK_CRIT_FILL_PERCENT"), 95),
        )


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mtproxy_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            total_connections INTEGER NOT NULL,
            unique_ips INTEGER NOT NULL,
            bytes_in INTEGER NOT NULL DEFAULT 0,
            bytes_out INTEGER NOT NULL DEFAULT 0,
            top_ips_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mtproxy_metrics_ts ON mtproxy_metrics(ts);

        CREATE TABLE IF NOT EXISTS mtproxy_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            alert_type TEXT NOT NULL,
            alert_key TEXT NOT NULL,
            message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mtproxy_alerts_ts ON mtproxy_alerts(ts);

        CREATE TABLE IF NOT EXISTS mtproxy_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS mtproxy_ip_geo_cache (
            ip TEXT PRIMARY KEY,
            data TEXT,
            ts INTEGER
        );
        """
    )
    conn.commit()


def _state_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM mtproxy_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _state_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO mtproxy_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def load_thresholds(conn: sqlite3.Connection, cfg: MtproxyConfig) -> tuple[int, int]:
    warn = _to_int(_state_get(conn, "threshold_max_connections_per_ip"), cfg.max_connections_per_ip)
    crit = _to_int(_state_get(conn, "threshold_max_unique_ips"), cfg.max_unique_ips)
    return max(1, warn), max(1, crit)


def update_threshold(conn: sqlite3.Connection, param: str, value: int) -> str:
    if value <= 0:
        return "Invalid value. Must be a positive integer."
    if param in {"warning", "max_connections_per_ip"}:
        _state_set(conn, "threshold_max_connections_per_ip", str(value))
        return f"MTPROXY threshold 'max_connections_per_ip' updated to {value}."
    if param in {"critical", "max_unique_ips"}:
        _state_set(conn, "threshold_max_unique_ips", str(value))
        return f"MTPROXY threshold 'max_unique_ips' updated to {value}."
    return "Unknown parameter. Use warning|critical."


def collect_connections(port: int) -> dict[str, Any]:
    per_ip: dict[str, int] = defaultdict(int)
    total = 0
    try:
        result = subprocess.run(
            ["ss", "-tn", "sport", "=", f":{port}"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = result.stdout.strip().splitlines()
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            peer = parts[4]
            if peer.startswith("["):
                ip = peer[1 : peer.rfind("]")]
            else:
                ip = peer.rsplit(":", 1)[0]
            per_ip[ip] += 1
            total += 1
    except (OSError, subprocess.SubprocessError):
        pass
    return {"total": total, "unique_ips": len(per_ip), "per_ip": dict(per_ip)}


def collect_traffic(conn: sqlite3.Connection, port: int) -> dict[str, int]:
    current_in = 0
    current_out = 0
    try:
        result = subprocess.run(
            ["iptables", "-L", "MTPROXY_MONITOR", "-n", "-v", "-x"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    bytes_count = int(parts[1])
                except ValueError:
                    continue
                if f"dpt:{port}" in line:
                    current_in += bytes_count
                elif f"spt:{port}" in line:
                    current_out += bytes_count
    except OSError:
        pass

    prev_in = _to_int(_state_get(conn, "prev_bytes_in"), 0)
    prev_out = _to_int(_state_get(conn, "prev_bytes_out"), 0)
    delta_in = current_in - prev_in if current_in >= prev_in else current_in
    delta_out = current_out - prev_out if current_out >= prev_out else current_out
    _state_set(conn, "prev_bytes_in", str(current_in))
    _state_set(conn, "prev_bytes_out", str(current_out))
    return {"bytes_in": max(0, delta_in), "bytes_out": max(0, delta_out)}


def check_mtproxy_alive() -> bool:
    try:
        result = subprocess.run(["pgrep", "mtproto-proxy"], capture_output=True, check=False)
        return result.returncode == 0
    except OSError:
        return False


def collect_conntrack() -> dict[str, float | int] | None:
    try:
        count = int(Path("/proc/sys/net/netfilter/nf_conntrack_count").read_text(encoding="utf-8").strip())
        maxv = int(Path("/proc/sys/net/netfilter/nf_conntrack_max").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if maxv <= 0:
        return None
    return {"count": count, "max": maxv, "percent": 100.0 * count / maxv}


def store_metric(conn: sqlite3.Connection, conns: dict[str, Any], traffic: dict[str, int]) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO mtproxy_metrics (ts, total_connections, unique_ips, bytes_in, bytes_out, top_ips_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            int(conns.get("total", 0)),
            int(conns.get("unique_ips", 0)),
            int(traffic.get("bytes_in", 0)),
            int(traffic.get("bytes_out", 0)),
            json.dumps(conns.get("per_ip", {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def _can_send_alert(conn: sqlite3.Connection, alert_type: str, alert_key: str, cooldown_min: int) -> bool:
    now = int(time.time())
    row = conn.execute(
        "SELECT ts FROM mtproxy_alerts WHERE alert_type = ? AND alert_key = ? ORDER BY ts DESC LIMIT 1",
        (alert_type, alert_key),
    ).fetchone()
    if row is None:
        return True
    return (now - int(row[0])) >= max(0, cooldown_min) * 60


def _record_alert(conn: sqlite3.Connection, alert_type: str, alert_key: str, message: str) -> None:
    conn.execute(
        "INSERT INTO mtproxy_alerts (ts, alert_type, alert_key, message) VALUES (?, ?, ?, ?)",
        (int(time.time()), alert_type, alert_key, message),
    )
    conn.commit()


def _query_geo_batch(ips: list[str]) -> dict[str, str]:
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
        if row and now - _to_int(str(row[1]), 0) <= _GEO_TTL_SEC:
            result[ip] = str(row[0] or "")
        else:
            pending.append(ip)
    for i in range(0, len(pending), 100):
        chunk = pending[i : i + 100]
        chunk_map = _query_geo_batch(chunk)
        for ip, geo in chunk_map.items():
            conn.execute(
                "INSERT INTO mtproxy_ip_geo_cache (ip, data, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                (ip, geo, now),
            )
            result[ip] = geo
    conn.commit()
    return result


def evaluate_alerts(
    conn: sqlite3.Connection,
    cfg: MtproxyConfig,
    conns: dict[str, Any],
    traffic: dict[str, int],
) -> list[str]:
    out: list[str] = []
    warn_per_ip, crit_unique = load_thresholds(conn, cfg)
    total = int(conns.get("total", 0))
    unique_ips = int(conns.get("unique_ips", 0))
    per_ip = dict(conns.get("per_ip", {}))

    if not check_mtproxy_alive():
        if _can_send_alert(conn, "down", "global", cfg.alert_cooldown_minutes):
            msg = "MTProxy DOWN\n\nProcess mtproto-proxy was not found."
            _record_alert(conn, "down", "global", msg)
            out.append(msg)
        return out

    if cfg.conntrack_enabled:
        ct = collect_conntrack()
        if ct is not None:
            pct = float(ct["percent"])
            count = int(ct["count"])
            maxv = int(ct["max"])
            if pct >= cfg.conntrack_crit_fill_percent and _can_send_alert(
                conn, "conntrack_critical", "global", cfg.alert_cooldown_minutes
            ):
                msg = f"Conntrack CRITICAL\n\nFill: {pct:.1f}% ({count}/{maxv})"
                _record_alert(conn, "conntrack_critical", "global", msg)
                out.append(msg)
            elif pct >= cfg.conntrack_warn_fill_percent and _can_send_alert(
                conn, "conntrack_warning", "global", cfg.alert_cooldown_minutes
            ):
                msg = f"Conntrack WARNING\n\nFill: {pct:.1f}% ({count}/{maxv})"
                _record_alert(conn, "conntrack_warning", "global", msg)
                out.append(msg)

    geo_map = get_ips_geo_info(conn, list(per_ip.keys()))

    for ip, count in per_ip.items():
        if int(count) <= warn_per_ip:
            continue
        if not _can_send_alert(conn, "warning_ip", ip, cfg.alert_cooldown_minutes):
            continue
        msg = (
            f"MTProxy WARNING\n\n"
            f"IP {ip}{geo_map.get(ip, '')} - {count} connections (threshold: {warn_per_ip})\n\n"
            f"Total: {total} conn | {unique_ips} unique IPs\n"
            f"Traffic per interval: Down {format_bytes(traffic['bytes_out'])} | Up {format_bytes(traffic['bytes_in'])}"
        )
        _record_alert(conn, "warning_ip", ip, msg)
        out.append(msg)

    if unique_ips > crit_unique and _can_send_alert(conn, "critical_leak", "global", cfg.alert_cooldown_minutes):
        sorted_ips = sorted(per_ip.items(), key=lambda x: x[1], reverse=True)[:10]
        top_str = "\n".join(f"  {ip}{geo_map.get(ip, '')} - {count} conn" for ip, count in sorted_ips)
        msg = (
            f"MTProxy LEAK ALERT\n\n"
            f"Detected {unique_ips} unique IPs (threshold: {crit_unique})\n\n"
            f"Top 10:\n{top_str}\n\n"
            f"Traffic per interval: Down {format_bytes(traffic['bytes_out'])} | Up {format_bytes(traffic['bytes_in'])}"
        )
        _record_alert(conn, "critical_leak", "global", msg)
        out.append(msg)

    return out


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


def summary_rows(conn: sqlite3.Connection, start_ts: int) -> list[tuple]:
    return list(
        conn.execute(
            """
            SELECT ts, total_connections, unique_ips, bytes_in, bytes_out, top_ips_json
            FROM mtproxy_metrics
            WHERE ts > ?
            ORDER BY ts
            """,
            (start_ts,),
        ).fetchall()
    )


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

