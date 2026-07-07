"""CLI for 24h leak investigation profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cock_monitor.adapters.linux_host import read_hostname_fqdn
from cock_monitor.modules.incident.env import load_env_overwrite
from cock_monitor.modules.incident.leak_profile import (
    build_leak_investigation_report,
    load_leak_state,
    start_leak_investigation,
    stop_leak_investigation,
)
from cock_monitor.modules.incident.postmortem import send_telegram


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="24h leak investigation profile")
    p.add_argument("action", choices=["start", "stop", "status", "report"])
    p.add_argument("--env-file", default="/etc/cock-monitor.env")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--send-telegram", action="store_true")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    env_path = Path(args.env_file).expanduser().resolve()
    if not env_path.is_file():
        print(f"leak-investigation: config not found: {env_path}", file=sys.stderr)
        return 1
    load_env_overwrite(env_path)

    if args.action == "start":
        st = start_leak_investigation(hours=args.hours)
        print(
            f"leak investigation started: start_ts={st['start_ts']} end_ts={st['end_ts']} "
            f"(enable cock-monitor-leak-investigation.timer for 60s sampling)"
        )
        return 0

    if args.action == "stop":
        stop_leak_investigation()
        print("leak investigation stopped")
        return 0

    st = load_leak_state()
    if args.action == "status":
        print(
            f"active={st.get('active')} start_ts={st.get('start_ts')} "
            f"end_ts={st.get('end_ts')} report_sent={st.get('report_sent')}"
        )
        return 0

    start_ts = int(st.get("start_ts", "0") or "0")
    end_ts = int(st.get("end_ts", "0") or "0")
    if start_ts <= 0:
        print("leak-investigation: no investigation window (run start first)", file=sys.stderr)
        return 2
    import time

    now = int(time.time())
    report_end = min(now, end_ts) if end_ts > 0 else now
    host = read_hostname_fqdn()
    body = build_leak_investigation_report(host=host, start_ts=start_ts, end_ts=report_end)
    print(body.replace("<b>", "").replace("</b>", ""))
    if args.send_telegram:
        result = send_telegram(body, parse_mode="HTML")
        if not result.success:
            print(f"leak-investigation: telegram failed: {result.reason}", file=sys.stderr)
            return 1
    return 0
