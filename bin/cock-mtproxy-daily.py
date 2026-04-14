#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mtproxy_module.charts import generate_mtproxy_chart
from mtproxy_module.core import MtproxyConfig, build_period_caption, connect_db, init_schema, summary_rows
from telegram_bot.telegram_client import TelegramClient


MSK_TZ = timezone(timedelta(hours=3), name="MSK")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="cock-monitor mtproxy daily report")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/cock-monitor.env"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        print(f"cock-mtproxy-daily: env file not found: {env_path}", file=sys.stderr)
        return 1
    raw = _parse_env_file(env_path)
    cfg = MtproxyConfig.from_env_map(raw)
    if not cfg.enabled:
        return 0

    conn = connect_db(cfg.db_path)
    init_schema(conn)
    start_ts = int(time.time()) - max(1, args.hours) * 3600
    rows = summary_rows(conn, start_ts)
    title = f"MTProxy Load - {datetime.now(MSK_TZ).strftime('%d.%m.%Y')}"

    out_path = args.output
    tmp_path: Path | None = None
    if out_path is None:
        fd, p = tempfile.mkstemp(prefix="cock-mtproxy-", suffix=".png")
        os.close(fd)
        tmp_path = Path(p)
        out_path = tmp_path

    try:
        generate_mtproxy_chart(rows, out_path, title=title)
    except ImportError as e:
        print("cock-mtproxy-daily: matplotlib required", file=sys.stderr)
        print(str(e), file=sys.stderr)
        conn.close()
        return 1
    caption = build_period_caption(conn, start_ts, title=f"MTProxy - Report ({args.hours}h)", top_n=cfg.daily_report_top_n)

    if args.send_telegram:
        token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            print("cock-mtproxy-daily: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
            conn.close()
            return 1
        client = TelegramClient(token)
        try:
            client.send_photo(chat_id, out_path, caption=caption)
        except RuntimeError as e:
            print(f"cock-mtproxy-daily: {e}", file=sys.stderr)
            conn.close()
            return 1

    conn.close()
    if tmp_path is not None:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

