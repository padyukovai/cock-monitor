#!/usr/bin/env python3
"""Build and send VLESS traffic reports from 3x-ui sqlite counters."""
from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone, tzinfo
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cock_monitor.defaults import DEFAULT_METRICS_DB
from cock_monitor.env import merge_env_into_process, parse_env_file

try:
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover - python < 3.9 fallback
    ZoneInfo = None  # type: ignore[misc,assignment]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_telegram_client():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from telegram_bot.telegram_client import TelegramClient

    return TelegramClient


def _get_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _load_tz(tz_name: str) -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    if tz_name == "Europe/Moscow":
        return timezone(timedelta(hours=3), name="MSK")
    if tz_name == "Asia/Tehran":
        return timezone(timedelta(hours=3, minutes=30), name="IRST")
    raise ValueError(f"invalid or unsupported timezone: {tz_name!r}")


@dataclass(frozen=True)
class IpParseStats:
    elapsed_ms: int
    bytes_read: int
    lines_matched: int
    truncated: bool


def _read_file_slice(path: Path, max_bytes: int, *, from_tail: bool) -> tuple[bytes, bool]:
    """Read up to max_bytes from file start (daily) or end (since-last). Returns (data, truncated)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= max_bytes:
                f.seek(0)
                return f.read(), False
            if from_tail:
                start = max(0, size - max_bytes)
                f.seek(start)
                data = f.read()
                truncated = start > 0
                if truncated and b"\n" in data:
                    nl = data.find(b"\n")
                    data = data[nl + 1 :]
                return data, truncated
            f.seek(0)
            data = f.read(max_bytes)
            return data, True
    except OSError:
        return b"", False


def _parse_access_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _extract_ip_from_from_field(line: str) -> str | None:
    idx = line.find(" from ")
    if idx < 0:
        return None
    rest = line[idx + 6 :].strip()
    if not rest:
        return None
    token = rest.split()[0]
    if token.startswith(("tcp:", "udp:")):
        parts = token.split(":", 1)
        if len(parts) < 2:
            return None
        token = parts[1]
    if token.startswith("["):
        close = token.find("]")
        if close < 0:
            return None
        return token[1:close].strip()
    if ":" in token:
        host, _, _ = token.rpartition(":")
        return host.strip() or None
    return None


def _extract_access_email(line: str) -> str | None:
    if " email:" not in line:
        return None
    tail = line.rsplit(" email:", 1)[-1].strip()
    if not tail:
        return None
    return tail.split()[0].strip()


def _normalize_client_ip(raw: str) -> tuple[str, str] | None:
    """Return (family, value) where family is '4' or '6' and value is normalized string."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if addr.version == 4:
        return ("4", str(addr))
    mapped = ipaddress.IPv6Address(addr).ipv4_mapped
    if mapped is not None:
        return ("4", str(mapped))
    return ("6", str(ipaddress.IPv6Address(addr).compressed))


def aggregate_vless_access_ips(
    paths: list[Path],
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    window_left_exclusive: bool,
    log_tz: tzinfo,
    allowed_emails: set[str],
    max_bytes_per_file: int,
    read_from_tail: bool,
) -> tuple[dict[str, tuple[set[str], set[str]]], IpParseStats]:
    """Aggregate unique IPv4/IPv6 per email from Xray access.log-style lines."""
    t0 = time.perf_counter()
    combined: dict[str, tuple[set[str], set[str]]] = {}
    bytes_read = 0
    lines_matched = 0
    truncated_any = False

    for path in paths:
        if not path.is_file():
            continue
        raw, truncated = _read_file_slice(path, max_bytes_per_file, from_tail=read_from_tail)
        bytes_read += len(raw)
        truncated_any = truncated_any or truncated
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if " email:" not in line:
                continue
            ts_end = line.find(" from ")
            if ts_end < 0:
                continue
            ts_raw = line[:ts_end].strip()
            dt_naive = _parse_access_ts(ts_raw)
            if dt_naive is None:
                continue
            if dt_naive.tzinfo is not None:
                dt_naive = dt_naive.replace(tzinfo=None)
            log_dt = dt_naive.replace(tzinfo=log_tz).astimezone(timezone.utc)
            if window_left_exclusive:
                if log_dt > window_end_utc:
                    break
                if not (log_dt > window_start_utc and log_dt <= window_end_utc):
                    continue
            else:
                if log_dt >= window_end_utc:
                    break
                if not (log_dt >= window_start_utc and log_dt < window_end_utc):
                    continue
            email = _extract_access_email(line)
            if not email or email not in allowed_emails:
                continue
            ip_raw = _extract_ip_from_from_field(line)
            if not ip_raw:
                continue
            norm = _normalize_client_ip(ip_raw)
            if norm is None:
                continue
            fam, val = norm
            if email not in combined:
                combined[email] = (set(), set())
            v4s, v6s = combined[email]
            if fam == "4":
                v4s.add(val)
            else:
                v6s.add(val)
            lines_matched += 1

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return combined, IpParseStats(
        elapsed_ms=elapsed_ms,
        bytes_read=bytes_read,
        lines_matched=lines_matched,
        truncated=truncated_any,
    )


def _daily_window_utc(prev_day_iso: str, snapshot_day_iso: str, tz_daily: tzinfo) -> tuple[datetime, datetime]:
    d0 = date.fromisoformat(prev_day_iso)
    d1 = date.fromisoformat(snapshot_day_iso)
    start = datetime.combine(d0, dt_time.min, tzinfo=tz_daily).astimezone(timezone.utc)
    end = datetime.combine(d1, dt_time.min, tzinfo=tz_daily).astimezone(timezone.utc)
    return start, end


def _shrink_telegram_html(text: str, max_len: int = 4000) -> str:
    if len(text) <= max_len:
        return text
    cut = re.sub(
        r"\n\n<b>Top \d+ by unique IP.*?(?=\n\n<b>Potential heavy|\n\n<i>Note:|\Z)",
        "",
        text,
        flags=re.S,
    )
    if len(cut) > max_len:
        cut = re.sub(
            r"\n\n<b>Potential heavy downloaders</b>:.*?(?=\n\n<i>Note:|\Z)",
            "",
            cut,
            flags=re.S,
        )
    if len(cut) > max_len:
        cut = cut[: max_len - 3] + "..."
    return cut


def _fmt_bytes(num: int) -> str:
    if num < 0:
        num = 0
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(num)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx <= 1:
        return f"{int(value)} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


@dataclass(frozen=True)
class TrafficRow:
    email: str
    up: int
    down: int

    @property
    def total(self) -> int:
        return self.up + self.down


def _safe_i64(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _extract_vless_emails(settings_text: str) -> set[str]:
    out: set[str] = set()
    try:
        payload = json.loads(settings_text or "{}")
    except json.JSONDecodeError:
        return out
    clients = payload.get("clients")
    if not isinstance(clients, list):
        return out
    for client in clients:
        if not isinstance(client, dict):
            continue
        email = str(client.get("email", "")).strip()
        if email:
            out.add(email)
    return out


def fetch_vless_email_set(conn: sqlite3.Connection) -> set[str]:
    emails: set[str] = set()
    cur = conn.execute(
        """
        SELECT protocol, settings
        FROM inbounds
        WHERE protocol IS NOT NULL
        """
    )
    for protocol, settings in cur.fetchall():
        if str(protocol).strip().lower() != "vless":
            continue
        if not isinstance(settings, str):
            continue
        emails.update(_extract_vless_emails(settings))
    return emails


def fetch_client_traffics(conn: sqlite3.Connection) -> list[TrafficRow]:
    cur = conn.execute(
        """
        SELECT email, COALESCE(up, 0) AS up_bytes, COALESCE(down, 0) AS down_bytes
        FROM client_traffics
        WHERE email IS NOT NULL
          AND TRIM(email) <> ''
        """
    )
    rows: list[TrafficRow] = []
    for email, up, down in cur.fetchall():
        rows.append(TrafficRow(email=str(email).strip(), up=_safe_i64(up), down=_safe_i64(down)))
    return rows


def ensure_report_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_daily_snapshots (
            snapshot_day_msk TEXT NOT NULL,
            ts INTEGER NOT NULL,
            email TEXT NOT NULL,
            up_bytes INTEGER NOT NULL,
            down_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            PRIMARY KEY (snapshot_day_msk, email)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_daily_snapshots_ts
        ON vless_daily_snapshots(ts)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_daily_reports (
            snapshot_day_msk TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            total_clients INTEGER NOT NULL,
            total_delta_bytes INTEGER NOT NULL,
            top1_email TEXT NOT NULL,
            top1_delta_bytes INTEGER NOT NULL,
            sent_ok INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vless_report_checkpoints (
            ts INTEGER NOT NULL,
            email TEXT NOT NULL,
            total_bytes INTEGER NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (ts, email)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vless_report_checkpoints_ts
        ON vless_report_checkpoints(ts)
        """
    )
    conn.commit()


def upsert_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_day_msk: str,
    ts: int,
    rows: list[TrafficRow],
) -> None:
    conn.executemany(
        """
        INSERT INTO vless_daily_snapshots (
            snapshot_day_msk, ts, email, up_bytes, down_bytes, total_bytes
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_day_msk, email) DO UPDATE SET
            ts = excluded.ts,
            up_bytes = excluded.up_bytes,
            down_bytes = excluded.down_bytes,
            total_bytes = excluded.total_bytes
        """,
        [(snapshot_day_msk, ts, r.email, r.up, r.down, r.total) for r in rows],
    )
    conn.commit()


def get_snapshot_map(conn: sqlite3.Connection, day_msk: str) -> dict[str, int]:
    cur = conn.execute(
        """
        SELECT email, total_bytes
        FROM vless_daily_snapshots
        WHERE snapshot_day_msk = ?
        """,
        (day_msk,),
    )
    out: dict[str, int] = {}
    for email, total in cur.fetchall():
        out[str(email)] = _safe_i64(total)
    return out


def get_last_sent_checkpoint_ts(conn: sqlite3.Connection, *, source: str) -> int | None:
    cur = conn.execute(
        """
        SELECT MAX(ts)
        FROM vless_report_checkpoints
        WHERE source = ?
        """
        ,
        (source,),
    )
    row = cur.fetchone()
    if not row:
        return None
    value = row[0]
    if value is None:
        return None
    return _safe_i64(value)


def get_checkpoint_map(conn: sqlite3.Connection, ts: int) -> dict[str, int]:
    cur = conn.execute(
        """
        SELECT email, total_bytes
        FROM vless_report_checkpoints
        WHERE ts = ?
        """,
        (ts,),
    )
    out: dict[str, int] = {}
    for email, total in cur.fetchall():
        out[str(email)] = _safe_i64(total)
    return out


def save_checkpoint(
    conn: sqlite3.Connection,
    *,
    ts: int,
    rows: list[TrafficRow],
    source: str,
) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO vless_report_checkpoints (ts, email, total_bytes, source)
        VALUES (?, ?, ?, ?)
        """,
        [(ts, r.email, r.total, source) for r in rows],
    )
    conn.commit()


def build_report(
    *,
    host: str,
    title: str,
    subtitle: str,
    current_map: dict[str, int],
    prev_map: dict[str, int],
    top_n: int,
    abuse_gb: float,
    abuse_share_pct: float,
    min_total_mb: int,
    ip_counts: dict[str, tuple[int, int]] | None = None,
    ip_top_k: int = 3,
    ip_truncated: bool = False,
) -> tuple[str, int, int, str, int]:
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    if not prev_map:
        text = (
            f"<b>{esc(host)} — {esc(title)}</b>\n"
            f"<i>{esc(subtitle)}</i>\n\n"
            "Baseline recorded now. Report will start from the next successful send."
        )
        return text, 0, 0, "", 0

    deltas: list[tuple[str, int, bool]] = []
    total_delta = 0
    reset_count = 0
    for email, cur_total in current_map.items():
        prev_total = prev_map.get(email, 0)
        raw_delta = cur_total - prev_total
        reset_detected = raw_delta < 0
        delta = raw_delta if raw_delta > 0 else 0
        if reset_detected:
            reset_count += 1
        if delta > 0:
            total_delta += delta
        deltas.append((email, delta, reset_detected))

    deltas.sort(key=lambda x: x[1], reverse=True)
    active = sum(1 for _, d, _ in deltas if d > 0)
    top = deltas[: max(1, top_n)]
    top1_email = top[0][0] if top else ""
    top1_delta = top[0][1] if top else 0
    top1_share = (top1_delta * 100.0 / total_delta) if total_delta > 0 else 0.0

    delta_lookup: dict[str, int] = {e: d for e, d, _ in deltas}

    lines: list[str] = []
    lines.append(f"<b>{esc(host)} — {esc(title)}</b>")
    lines.append(f"<i>{esc(subtitle)}</i>")
    lines.append(
        f"\n<b>Total:</b> <code>{_fmt_bytes(total_delta)}</code> | "
        f"<b>Active clients:</b> <code>{active}</code> | "
        f"<b>Top1 share:</b> <code>{top1_share:.1f}%</code>"
    )

    lines.append("")
    lines.append(f"<b>Top {max(1, top_n)} downloaders</b>:")
    rank = 0
    for email, delta, _ in top:
        if delta <= 0:
            continue
        rank += 1
        share = (delta * 100.0 / total_delta) if total_delta > 0 else 0.0
        ip_suffix = ""
        if ip_counts is not None:
            n4, n6 = ip_counts.get(email, (0, 0))
            ip_suffix = (
                f" | <b>IP4</b> <code>{n4}</code> <b>IP6</b> <code>{n6}</code> "
                f"<b>IPΣ</b> <code>{n4 + n6}</code>"
            )
        lines.append(
            f"{rank}) <code>{esc(email)}</code> — <b>{_fmt_bytes(delta)}</b> "
            f"(<code>{share:.1f}%</code>){ip_suffix}"
        )
    if rank == 0:
        lines.append("No positive usage detected for this day.")

    if ip_counts is not None and ip_top_k > 0:
        ip_candidates: list[tuple[str, int, int, int, int]] = []
        for email, (n4, n6) in ip_counts.items():
            sigma = n4 + n6
            if sigma <= 0:
                continue
            ip_candidates.append(
                (email, sigma, n4, n6, int(delta_lookup.get(email, 0)))
            )
        ip_candidates.sort(key=lambda x: (-x[1], -x[4]))
        top_ip = ip_candidates[: max(1, ip_top_k)]
        if top_ip:
            lines.append("")
            lines.append(
                f"<b>Top {max(1, ip_top_k)} by unique IP (IPv4+IPv6)</b>:"
            )
            for i, (email, sigma, n4, n6, dlt) in enumerate(top_ip, start=1):
                lines.append(
                    f"{i}) <code>{esc(email)}</code> — <b>IPΣ</b> <code>{sigma}</code> "
                    f"(<b>IP4</b> <code>{n4}</code> <b>IP6</b> <code>{n6}</code>) "
                    f"| <b>traffic Δ</b> <code>{_fmt_bytes(dlt)}</code>"
                )
        if ip_truncated:
            lines.append("")
            lines.append(
                "<i>Note: IP log tail truncated by VLESS_IP_PARSE_MAX_MB; counts may be incomplete.</i>"
            )

    threshold_bytes = int(abuse_gb * 1024 * 1024 * 1024)
    min_total_bytes = int(min_total_mb * 1024 * 1024)
    abuse: list[str] = []
    for email, delta, _ in deltas:
        if delta <= 0:
            continue
        share = (delta * 100.0 / total_delta) if total_delta > 0 else 0.0
        by_abs = threshold_bytes > 0 and delta >= threshold_bytes
        by_share = (
            abuse_share_pct > 0
            and total_delta >= min_total_bytes
            and share >= abuse_share_pct
        )
        if by_abs or by_share:
            abuse.append(
                f"- <code>{esc(email)}</code>: <b>{_fmt_bytes(delta)}</b> "
                f"(<code>{share:.1f}%</code>)"
            )

    if abuse:
        lines.append("")
        lines.append("<b>Potential heavy downloaders</b>:")
        lines.extend(abuse)

    if reset_count > 0:
        lines.append("")
        lines.append(
            f"<i>Note: reset/anomaly detected for {reset_count} client(s), "
            "negative deltas clamped to 0.</i>"
        )

    text = "\n".join(lines)
    text = _shrink_telegram_html(text, max_len=4000)
    return text, active, total_delta, top1_email, top1_delta


def save_report_meta(
    conn: sqlite3.Connection,
    *,
    snapshot_day_msk: str,
    ts: int,
    total_clients: int,
    total_delta_bytes: int,
    top1_email: str,
    top1_delta_bytes: int,
    sent_ok: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO vless_daily_reports (
            snapshot_day_msk, ts, total_clients, total_delta_bytes,
            top1_email, top1_delta_bytes, sent_ok
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_day_msk) DO UPDATE SET
            ts = excluded.ts,
            total_clients = excluded.total_clients,
            total_delta_bytes = excluded.total_delta_bytes,
            top1_email = excluded.top1_email,
            top1_delta_bytes = excluded.top1_delta_bytes,
            sent_ok = excluded.sent_ok
        """,
        (
            snapshot_day_msk,
            ts,
            total_clients,
            total_delta_bytes,
            top1_email,
            top1_delta_bytes,
            1 if sent_ok else 0,
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor VLESS daily usage report")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/cock-monitor.env"),
        help="Env file with XUI_DB_PATH, METRICS_DB and optional Telegram vars",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send report to Telegram (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)",
    )
    parser.add_argument(
        "--mode",
        choices=("since-last-sent", "daily"),
        default="since-last-sent",
        help="Report mode: since-last-sent (default) or daily (D vs D-1 in report TZ)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without Telegram send",
    )
    args = parser.parse_args()

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-vless-daily-report: env file not found: {env_path}", file=sys.stderr)
        return 1

    raw = parse_env_file(env_path)
    merge_env_into_process(raw)

    xui_db_path = os.environ.get("XUI_DB_PATH", "").strip()
    if not xui_db_path:
        print("cock-vless-daily-report: XUI_DB_PATH is required", file=sys.stderr)
        return 1
    metrics_db = os.environ.get("METRICS_DB", DEFAULT_METRICS_DB).strip()
    tz_name = os.environ.get("VLESS_DAILY_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    top_n = max(1, _get_int_env("VLESS_DAILY_TOP_N", 10))
    abuse_gb = max(0.0, _get_float_env("VLESS_ABUSE_GB", 20.0))
    abuse_share_pct = max(0.0, _get_float_env("VLESS_ABUSE_SHARE_PCT", 40.0))
    min_total_mb = max(0, _get_int_env("VLESS_DAILY_MIN_TOTAL_MB", 500))
    ip_top_k = max(1, _get_int_env("VLESS_IP_TOP_K", 3))
    ip_parse_max_mb = max(1, _get_int_env("VLESS_IP_PARSE_MAX_MB", 256))
    ip_parse_max_bytes = ip_parse_max_mb * 1024 * 1024

    try:
        tz = _load_tz(tz_name)
    except ValueError:
        print(f"cock-vless-daily-report: invalid VLESS_DAILY_TZ={tz_name!r}", file=sys.stderr)
        return 1

    telegram_tz_name = (
        os.environ.get("VLESS_TELEGRAM_DISPLAY_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    )
    try:
        telegram_display_tz = _load_tz(telegram_tz_name)
    except ValueError:
        telegram_display_tz = _load_tz("Europe/Moscow")
        telegram_tz_name = "Europe/Moscow"

    now = datetime.now(tz)
    snapshot_day = now.date().isoformat()
    prev_day = (now.date() - timedelta(days=1)).isoformat()
    usage_day = prev_day
    ts = int(time.time())

    xui_uri = f"file:{xui_db_path}?mode=ro"
    try:
        xui_conn = sqlite3.connect(xui_uri, uri=True)
    except sqlite3.Error as e:
        print(f"cock-vless-daily-report: open x-ui.db failed: {e}", file=sys.stderr)
        return 1

    try:
        all_rows = fetch_client_traffics(xui_conn)
        vless_emails = fetch_vless_email_set(xui_conn)
    except sqlite3.Error as e:
        print(f"cock-vless-daily-report: query x-ui.db failed: {e}", file=sys.stderr)
        return 1
    finally:
        xui_conn.close()

    if vless_emails:
        rows = [r for r in all_rows if r.email in vless_emails]
    else:
        # Fallback to all rows if protocol mapping is unavailable in this version/schema.
        rows = all_rows

    if not rows:
        print("cock-vless-daily-report: no client traffic rows found", file=sys.stderr)
        return 1

    try:
        met_conn = sqlite3.connect(metrics_db)
    except sqlite3.Error as e:
        print(f"cock-vless-daily-report: open METRICS_DB failed: {e}", file=sys.stderr)
        return 1

    report_text = ""
    active_clients = 0
    total_delta = 0
    top1_email = ""
    top1_delta = 0
    sent_ok = False
    try:
        ensure_report_tables(met_conn)
        upsert_snapshot(met_conn, snapshot_day_msk=snapshot_day, ts=ts, rows=rows)
        current_map = {r.email: r.total for r in rows}
        last_sent_ts: int | None = None
        if args.mode == "daily":
            prev_map = get_snapshot_map(met_conn, prev_day)
            report_title = f"VLESS daily usage ({tz_name}): {usage_day}"
            report_subtitle = "Delta period: previous day snapshot -> current snapshot"
        else:
            last_sent_ts = get_last_sent_checkpoint_ts(met_conn, source="since_last_sent")
            prev_map = get_checkpoint_map(met_conn, last_sent_ts) if last_sent_ts else {}
            if last_sent_ts:
                last_dt = (
                    datetime.fromtimestamp(last_sent_ts, tz=timezone.utc)
                    .astimezone(telegram_display_tz)
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
                report_subtitle = (
                    f"Delta period: since last sent report at {last_dt} ({telegram_tz_name})"
                )
            else:
                report_subtitle = "Delta period: since last sent report (no baseline yet)"
            report_title = f"VLESS delta since last report ({tz_name})"

        ip_counts: dict[str, tuple[int, int]] | None = None
        ip_truncated = False
        if prev_map:
            log_path_raw = os.environ.get("VLESS_ACCESS_LOG_PATH", "").strip()
            if log_path_raw:
                log_path = Path(log_path_raw)
                log_paths: list[Path] = []
                if log_path.is_file():
                    log_paths.append(log_path)
                prev_log = os.environ.get("VLESS_ACCESS_LOG_PATH_PREV", "").strip()
                if prev_log:
                    pp = Path(prev_log)
                    if pp.is_file():
                        log_paths.append(pp)
                else:
                    alt = Path(str(log_path) + ".1")
                    if alt.is_file():
                        log_paths.append(alt)
                if not log_paths:
                    print(
                        f"cock-vless-daily-report: VLESS_ACCESS_LOG_PATH not a readable file: {log_path_raw!r}",
                        file=sys.stderr,
                    )
                else:
                    log_tz_name = os.environ.get("VLESS_ACCESS_LOG_TZ", "").strip() or tz_name
                    try:
                        log_tz = _load_tz(log_tz_name)
                    except ValueError:
                        print(
                            f"cock-vless-daily-report: invalid VLESS_ACCESS_LOG_TZ={log_tz_name!r}",
                            file=sys.stderr,
                        )
                        log_tz = tz
                    allowed_emails = vless_emails if vless_emails else set(current_map.keys())
                    if args.mode == "daily":
                        w0, w1 = _daily_window_utc(prev_day, snapshot_day, tz)
                        agg, ip_stats = aggregate_vless_access_ips(
                            log_paths,
                            window_start_utc=w0,
                            window_end_utc=w1,
                            window_left_exclusive=False,
                            log_tz=log_tz,
                            allowed_emails=allowed_emails,
                            max_bytes_per_file=ip_parse_max_bytes,
                            read_from_tail=False,
                        )
                    else:
                        if last_sent_ts is not None:
                            end_ts = time.time()
                            w0 = datetime.fromtimestamp(last_sent_ts, tz=timezone.utc)
                            w1 = datetime.fromtimestamp(end_ts, tz=timezone.utc)
                            agg, ip_stats = aggregate_vless_access_ips(
                                log_paths,
                                window_start_utc=w0,
                                window_end_utc=w1,
                                window_left_exclusive=True,
                                log_tz=log_tz,
                                allowed_emails=allowed_emails,
                                max_bytes_per_file=ip_parse_max_bytes,
                                read_from_tail=True,
                            )
                        else:
                            agg, ip_stats = {}, IpParseStats(0, 0, 0, False)
                    ip_counts = (
                        {e: (len(v4), len(v6)) for e, (v4, v6) in agg.items()} if agg else None
                    )
                    ip_truncated = ip_stats.truncated
                    print(
                        "cock-vless-daily-report: "
                        f"ip_parse_ms={ip_stats.elapsed_ms} "
                        f"ip_bytes_read={ip_stats.bytes_read} "
                        f"ip_lines_matched={ip_stats.lines_matched} "
                        f"ip_truncated={1 if ip_stats.truncated else 0}",
                        file=sys.stderr,
                    )

        report_text, active_clients, total_delta, top1_email, top1_delta = build_report(
            host=socket.gethostname(),
            title=report_title,
            subtitle=report_subtitle,
            current_map=current_map,
            prev_map=prev_map,
            top_n=top_n,
            abuse_gb=abuse_gb,
            abuse_share_pct=abuse_share_pct,
            min_total_mb=min_total_mb,
            ip_counts=ip_counts,
            ip_top_k=ip_top_k,
            ip_truncated=ip_truncated,
        )

        if args.dry_run or not args.send_telegram:
            print(report_text)
            sent_ok = True
        else:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if not token or not chat:
                print(
                    "cock-vless-daily-report: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required",
                    file=sys.stderr,
                )
                return 1
            TelegramClient = _load_telegram_client()
            client = TelegramClient(token)
            client.send_message(chat, report_text, parse_mode="HTML")
            sent_ok = True
            if args.mode == "since-last-sent":
                save_checkpoint(met_conn, ts=ts, rows=rows, source="since_last_sent")
    except sqlite3.Error as e:
        print(f"cock-vless-daily-report: sqlite failure: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"cock-vless-daily-report: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            if report_text:
                save_report_meta(
                    met_conn,
                    snapshot_day_msk=snapshot_day,
                    ts=ts,
                    total_clients=active_clients,
                    total_delta_bytes=total_delta,
                    top1_email=top1_email,
                    top1_delta_bytes=top1_delta,
                    sent_ok=sent_ok,
                )
        finally:
            met_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
