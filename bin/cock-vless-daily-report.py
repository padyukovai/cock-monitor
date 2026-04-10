#!/usr/bin/env python3
"""Build and send VLESS traffic reports from 3x-ui sqlite counters."""
from __future__ import annotations

import argparse
import html
import json
import os
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

try:
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover - python < 3.9 fallback
    ZoneInfo = None  # type: ignore[misc,assignment]


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
    raise ValueError(f"invalid or unsupported timezone: {tz_name!r}")


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
        lines.append(
            f"{rank}) <code>{esc(email)}</code> — <b>{_fmt_bytes(delta)}</b> "
            f"(<code>{share:.1f}%</code>)"
        )
    if rank == 0:
        lines.append("No positive usage detected for this day.")

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
    if len(text) > 4000:
        text = text[:3997] + "..."
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

    raw = _parse_env_file(env_path)
    for k, v in raw.items():
        if k not in os.environ:
            os.environ[k] = v

    xui_db_path = os.environ.get("XUI_DB_PATH", "").strip()
    if not xui_db_path:
        print("cock-vless-daily-report: XUI_DB_PATH is required", file=sys.stderr)
        return 1
    metrics_db = os.environ.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db").strip()
    tz_name = os.environ.get("VLESS_DAILY_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    top_n = max(1, _get_int_env("VLESS_DAILY_TOP_N", 10))
    abuse_gb = max(0.0, _get_float_env("VLESS_ABUSE_GB", 20.0))
    abuse_share_pct = max(0.0, _get_float_env("VLESS_ABUSE_SHARE_PCT", 40.0))
    min_total_mb = max(0, _get_int_env("VLESS_DAILY_MIN_TOTAL_MB", 500))

    try:
        tz = _load_tz(tz_name)
    except ValueError:
        print(f"cock-vless-daily-report: invalid VLESS_DAILY_TZ={tz_name!r}", file=sys.stderr)
        return 1

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
        if args.mode == "daily":
            prev_map = get_snapshot_map(met_conn, prev_day)
            report_title = f"VLESS daily usage ({tz_name}): {usage_day}"
            report_subtitle = "Delta period: previous day snapshot -> current snapshot"
        else:
            last_sent_ts = get_last_sent_checkpoint_ts(met_conn, source="since_last_sent")
            prev_map = get_checkpoint_map(met_conn, last_sent_ts) if last_sent_ts else {}
            if last_sent_ts:
                last_dt = datetime.fromtimestamp(last_sent_ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S")
                report_subtitle = f"Delta period: since last sent report at {last_dt}"
            else:
                report_subtitle = "Delta period: since last sent report (no baseline yet)"
            report_title = f"VLESS delta since last report ({tz_name})"
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
