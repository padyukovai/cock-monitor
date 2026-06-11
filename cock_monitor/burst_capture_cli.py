"""CLI for burst-capture start/stop/status/report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cock_monitor.services import burst_capture, burst_report


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cock_monitor burst-capture",
        description="On-demand 1 Hz VPS burst diagnostics",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to cock-monitor env file",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="Start background capture")
    start_p.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Capture duration in seconds (default 60)",
    )

    sub.add_parser("stop", help="Stop running capture")
    sub.add_parser("status", help="Show capture status")

    report_p = sub.add_parser("report", help="Analyze burst JSONL")
    report_p.add_argument("jsonl_path", type=Path)
    report_p.add_argument("--json", action="store_true")
    report_p.add_argument("--client-failed", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "report":
        return burst_report.run_report(
            [str(args.jsonl_path)]
            + (["--json"] if args.json else [])
            + (["--client-failed"] if args.client_failed else [])
        )

    env_file = args.env_file
    if env_file is None:
        print("burst-capture: --env-file required for start/stop/status", file=sys.stderr)
        return 2
    if not env_file.is_file():
        print(f"burst-capture: env file not found: {env_file}", file=sys.stderr)
        return 1
    burst_capture.load_env_from_file(env_file.expanduser().resolve())

    if args.command == "start":
        return burst_capture.cmd_start(args.duration)
    if args.command == "stop":
        return burst_capture.cmd_stop()
    if args.command == "status":
        return burst_capture.cmd_status()
    return 2
