"""Analyze burst-capture JSONL and emit diagnostic verdicts."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_burst_samples(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return samples
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("sampler") != "burst-capture":
            continue
        samples.append(row)
    samples.sort(key=lambda r: int(r.get("ts_epoch", 0) or 0))
    return samples


def _nested_int(row: dict[str, Any], *keys: str) -> int:
    cur: Any = row
    for k in keys:
        if not isinstance(cur, dict):
            return 0
        cur = cur.get(k)
    if isinstance(cur, bool):
        return int(cur)
    if isinstance(cur, int):
        return cur
    if isinstance(cur, float):
        return int(cur)
    return 0


def _nested_float(row: dict[str, Any], *keys: str) -> float:
    cur: Any = row
    for k in keys:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(k)
    if isinstance(cur, (int, float)):
        return float(cur)
    return 0.0


def aggregate_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}
    peaks: dict[str, Any] = {
        "port443_estab": 0,
        "port443_syn_recv": 0,
        "ss_orphan": 0,
        "ss_estab": 0,
        "conntrack_fill_pct": 0,
        "listen_overflows": 0,
        "tcp_timeouts": 0,
        "xray_cpu_pct": 0.0,
        "xray_fds": 0,
        "total_accepted": 0,
        "total_from_ip": 0,
    }
    first_lo = last_lo = None
    for row in samples:
        peaks["port443_estab"] = max(peaks["port443_estab"], _nested_int(row, "port443", "estab"))
        peaks["port443_syn_recv"] = max(peaks["port443_syn_recv"], _nested_int(row, "port443", "syn_recv"))
        peaks["ss_orphan"] = max(peaks["ss_orphan"], _nested_int(row, "ss", "orphan"))
        peaks["ss_estab"] = max(peaks["ss_estab"], _nested_int(row, "ss", "estab"))
        peaks["conntrack_fill_pct"] = max(
            peaks["conntrack_fill_pct"], _nested_int(row, "conntrack", "fill_pct")
        )
        lo = _nested_int(row, "netstat", "ListenOverflows")
        peaks["listen_overflows"] = max(peaks["listen_overflows"], lo)
        peaks["tcp_timeouts"] = max(peaks["tcp_timeouts"], _nested_int(row, "netstat", "TCPTimeouts"))
        peaks["xray_cpu_pct"] = max(peaks["xray_cpu_pct"], _nested_float(row, "xray", "cpu_pct"))
        peaks["xray_fds"] = max(peaks["xray_fds"], _nested_int(row, "xray", "fds"))
        peaks["total_accepted"] += _nested_int(row, "access_log", "delta_accepted")
        peaks["total_from_ip"] += _nested_int(row, "access_log", "delta_from_ip")
        if first_lo is None:
            first_lo = lo
        last_lo = lo

    start_ts = int(samples[0].get("ts_epoch", 0) or 0)
    end_ts = int(samples[-1].get("ts_epoch", 0) or 0)
    host = str(samples[0].get("host", "unknown"))
    return {
        "host": host,
        "samples": len(samples),
        "window_start_ts": start_ts,
        "window_end_ts": end_ts,
        "peaks": peaks,
        "listen_overflows_delta": (last_lo or 0) - (first_lo or 0),
    }


def compute_verdict(
    agg: dict[str, Any],
    *,
    estab_threshold: int = 3,
    syn_recv_threshold: int = 5,
    conntrack_warn_pct: int = 85,
    client_failed: bool = False,
) -> tuple[str, list[str]]:
    if not agg:
        return "no_data", ["no burst-capture samples in file"]
    peaks = agg.get("peaks", {})
    reasons: list[str] = []
    p_estab = int(peaks.get("port443_estab", 0))
    total_acc = int(peaks.get("total_accepted", 0))
    fill = int(peaks.get("conntrack_fill_pct", 0))
    syn = int(peaks.get("port443_syn_recv", 0))
    lo = int(peaks.get("listen_overflows", 0))
    lo_delta = int(agg.get("listen_overflows_delta", 0))
    cpu = float(peaks.get("xray_cpu_pct", 0.0))
    fds = int(peaks.get("xray_fds", 0))

    if lo > 0 or lo_delta > 0:
        reasons.append(f"ListenOverflows peak={lo} delta={lo_delta}")
        return "syn_backlog", reasons

    if fill >= conntrack_warn_pct or syn >= syn_recv_threshold:
        reasons.append(f"conntrack fill_pct peak={fill} port443 syn_recv peak={syn}")
        return "conntrack_pressure", reasons

    if p_estab >= estab_threshold and total_acc == 0:
        orphan = int(peaks.get("ss_orphan", 0))
        reasons.append(
            f"port443 estab peak={p_estab} accepted_delta=0 ss_orphan peak={orphan}"
        )
        return "handshake_stall", reasons

    if total_acc > 0 and client_failed:
        reasons.append(f"accepted_delta={total_acc} but client reported failure")
        return "post_auth_failure", reasons

    if fds > 500 and cpu < 50:
        reasons.append(f"xray fds peak={fds} cpu peak={cpu}%")
        return "xray_saturated", reasons

    reasons.append(f"port443 estab peak={p_estab} accepted_delta={total_acc}")
    return "ok", reasons


def build_report(
    path: Path,
    *,
    client_failed: bool = False,
    json_out: bool = False,
) -> dict[str, Any]:
    samples = load_burst_samples(path)
    agg = aggregate_samples(samples)
    verdict, reasons = compute_verdict(agg, client_failed=client_failed)
    report = {
        "verdict": verdict,
        "reasons": reasons,
        "evidence": agg,
        "file": str(path),
    }
    if json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        host = agg.get("host", "unknown") if agg else "unknown"
        n = agg.get("samples", 0) if agg else 0
        print(f"Burst report: {host} ({n} samples)")
        print(f"Verdict: {verdict}")
        for r in reasons:
            print(f"  - {r}")
        if agg:
            peaks = agg.get("peaks", {})
            print(
                "Peaks: "
                f"port443_estab={peaks.get('port443_estab')} "
                f"syn_recv={peaks.get('port443_syn_recv')} "
                f"orphan={peaks.get('ss_orphan')} "
                f"accepted_total={peaks.get('total_accepted')} "
                f"conntrack_fill={peaks.get('conntrack_fill_pct')}%"
            )
    return report


def run_report(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze burst-capture JSONL")
    parser.add_argument("jsonl_path", type=Path, help="Path to burst-*.jsonl")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--client-failed",
        action="store_true",
        help="Client test reported failure (enables post_auth_failure verdict)",
    )
    args = parser.parse_args(argv)
    if not args.jsonl_path.is_file():
        print(f"burst-report: file not found: {args.jsonl_path}", file=sys.stderr)
        return 1
    build_report(args.jsonl_path, client_failed=args.client_failed, json_out=args.json)
    return 0
