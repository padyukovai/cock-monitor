"""CLI: python3 -m cock_monitor conntrack-storage migrate|read-last|write|write-from-env."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from cock_monitor.storage.conntrack_host_repository import (
    ConntrackHostRepository,
    ConntrackSampleInsert,
    HostSampleInsert,
)


def _migrate(db: Path) -> int:
    with ConntrackHostRepository.open(db) as _repo:
        pass
    return 0


def _read_last(db: Path) -> int:
    with ConntrackHostRepository.open(db) as repo:
        line = repo.read_last_stats_line()
    if line:
        sys.stdout.write(line + "\n")
    return 0


def _optional_int(x: Any) -> int | None:
    if x is None:
        return None
    if isinstance(x, bool):
        raise TypeError("booleans are not valid integers in JSON payload")
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    raise TypeError(f"expected int or null, got {type(x).__name__}")


def _optional_float(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, bool):
        raise TypeError("booleans are not valid floats in JSON payload")
    if isinstance(x, (int, float)):
        return float(x)
    raise TypeError(f"expected number or null, got {type(x).__name__}")


def _optional_str(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, str):
        return x
    raise TypeError(f"expected str or null, got {type(x).__name__}")


def run_write_from_dict(data: dict[str, Any]) -> None:
    db = Path(data["database"])
    now_ts = int(data["now_ts"])
    has_conntrack = bool(data.get("has_conntrack", False))
    retention_days = int(data.get("retention_days") or 0)
    max_rows = int(data.get("max_rows") or 0)
    retention_now_ts = int(data.get("retention_now_ts", time.time()))

    fill_pct = _optional_int(data.get("fill_pct"))
    fill_count = _optional_int(data.get("fill_count"))
    fill_max = _optional_int(data.get("fill_max"))

    if has_conntrack:
        sample = ConntrackSampleInsert(
            ts=now_ts,
            fill_pct=fill_pct,
            fill_count=fill_count,
            fill_max=fill_max,
            drop=int(data.get("drop", 0)),
            insert_failed=int(data.get("insert_failed", 0)),
            early_drop=int(data.get("early_drop", 0)),
            error=int(data.get("error", 0)),
            invalid=int(data.get("invalid", 0)),
            search_restart=int(data.get("search_restart", 0)),
            interval_sec=_optional_int(data.get("interval_sec")),
            delta_drop=_optional_int(data.get("delta_drop")),
            delta_insert_failed=_optional_int(data.get("delta_insert_failed")),
            delta_early_drop=_optional_int(data.get("delta_early_drop")),
            delta_error=_optional_int(data.get("delta_error")),
            delta_invalid=_optional_int(data.get("delta_invalid")),
            delta_search_restart=_optional_int(data.get("delta_search_restart")),
        )
    else:
        sample = ConntrackSampleInsert(
            ts=now_ts,
            fill_pct=fill_pct,
            fill_count=fill_count,
            fill_max=fill_max,
            drop=0,
            insert_failed=0,
            early_drop=0,
            error=0,
            invalid=0,
            search_restart=0,
            interval_sec=None,
            delta_drop=None,
            delta_insert_failed=None,
            delta_early_drop=None,
            delta_error=None,
            delta_invalid=None,
            delta_search_restart=None,
        )

    h = data.get("host") or {}
    host = HostSampleInsert(
        ts=now_ts,
        load1=_optional_float(h.get("load1")),
        mem_avail_kb=_optional_int(h.get("mem_avail_kb")),
        swap_used_kb=_optional_int(h.get("swap_used_kb")),
        tcp_inuse=_optional_int(h.get("tcp_inuse")),
        tcp_orphan=_optional_int(h.get("tcp_orphan")),
        tcp_tw=_optional_int(h.get("tcp_tw")),
        tcp6_inuse=_optional_int(h.get("tcp6_inuse")),
        shaper_rate_mbit=_optional_float(h.get("shaper_rate_mbit")),
        shaper_cpu_pct=_optional_int(h.get("shaper_cpu_pct")),
        tc_qdisc_root=_optional_str(h.get("tc_qdisc_root")),
    )

    with ConntrackHostRepository.open(db) as repo:
        repo.insert_sample_and_host(sample, host)
        if retention_days > 0:
            cutoff = retention_now_ts - retention_days * 86400
            repo.apply_retention(cutoff)
        if max_rows > 0:
            repo.trim_to_max_rows(max_rows)
        repo.delete_host_orphans()


def _write(db: Path) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("conntrack-storage: write: empty stdin (expected JSON)", file=sys.stderr)
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"conntrack-storage: invalid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("conntrack-storage: write: JSON must be an object", file=sys.stderr)
        return 2
    data = dict(data)
    data["database"] = str(db)
    try:
        run_write_from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        print(f"conntrack-storage: write: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"conntrack-storage: write: {e}", file=sys.stderr)
        return 1
    return 0


def _env_strip(name: str) -> str:
    return os.environ.get(name, "").strip()


def _env_int_req(name: str) -> int:
    v = _env_strip(name)
    if not v or v.upper() == "NULL":
        raise KeyError(name)
    return int(v)


def _env_int_or_empty_as_none(name: str) -> int | None:
    v = _env_strip(name)
    if not v or v.upper() == "NULL":
        return None
    return int(v)


def _env_float_or_none(name: str) -> float | None:
    v = _env_strip(name)
    if not v or v.upper() == "NULL":
        return None
    return float(v)


def build_write_payload_from_environ() -> dict[str, Any]:
    """Build write payload from COCK_MS_* env (set by check-conntrack.sh)."""
    fill_pct = _env_int_or_empty_as_none("COCK_MS_FILL_PCT")
    fill_count = _env_int_or_empty_as_none("COCK_MS_FILL_COUNT")
    fill_max = _env_int_or_empty_as_none("COCK_MS_FILL_MAX")
    tc_raw = os.environ.get("COCK_MS_HOST_TC_QDISC_ROOT")
    if tc_raw is None or not str(tc_raw).strip():
        tc = None
    else:
        tc = str(tc_raw)[:400]
    db = _env_strip("COCK_MS_DB")
    if not db:
        raise KeyError("COCK_MS_DB")
    return {
        "database": db,
        "now_ts": _env_int_req("COCK_MS_NOW_TS"),
        "has_conntrack": bool(int(_env_strip("COCK_MS_HAS_CT") or "0")),
        "retention_days": int(_env_strip("COCK_MS_RETENTION_DAYS") or "0"),
        "max_rows": int(_env_strip("COCK_MS_MAX_ROWS") or "0"),
        "retention_now_ts": int(_env_strip("COCK_MS_RETENTION_NOW_TS") or time.time()),
        "fill_pct": fill_pct,
        "fill_count": fill_count,
        "fill_max": fill_max,
        "drop": int(_env_strip("COCK_MS_DROP") or "0"),
        "insert_failed": int(_env_strip("COCK_MS_INSERT_FAILED") or "0"),
        "early_drop": int(_env_strip("COCK_MS_EARLY_DROP") or "0"),
        "error": int(_env_strip("COCK_MS_ERROR") or "0"),
        "invalid": int(_env_strip("COCK_MS_INVALID") or "0"),
        "search_restart": int(_env_strip("COCK_MS_SEARCH_RESTART") or "0"),
        "interval_sec": _env_int_or_empty_as_none("COCK_MS_INTERVAL_SEC"),
        "delta_drop": _env_int_or_empty_as_none("COCK_MS_DELTA_DROP"),
        "delta_insert_failed": _env_int_or_empty_as_none("COCK_MS_DELTA_INSERT_FAILED"),
        "delta_early_drop": _env_int_or_empty_as_none("COCK_MS_DELTA_EARLY_DROP"),
        "delta_error": _env_int_or_empty_as_none("COCK_MS_DELTA_ERROR"),
        "delta_invalid": _env_int_or_empty_as_none("COCK_MS_DELTA_INVALID"),
        "delta_search_restart": _env_int_or_empty_as_none("COCK_MS_DELTA_SEARCH_RESTART"),
        "host": {
            "load1": _env_float_or_none("COCK_MS_HOST_LOAD1"),
            "mem_avail_kb": _env_int_or_empty_as_none("COCK_MS_HOST_MEM_AVAIL_KB"),
            "swap_used_kb": _env_int_or_empty_as_none("COCK_MS_HOST_SWAP_USED_KB"),
            "tcp_inuse": _env_int_or_empty_as_none("COCK_MS_HOST_TCP_INUSE"),
            "tcp_orphan": _env_int_or_empty_as_none("COCK_MS_HOST_TCP_ORPHAN"),
            "tcp_tw": _env_int_or_empty_as_none("COCK_MS_HOST_TCP_TW"),
            "tcp6_inuse": _env_int_or_empty_as_none("COCK_MS_HOST_TCP6_INUSE"),
            "shaper_rate_mbit": _env_float_or_none("COCK_MS_HOST_SHAPER_RATE_MBIT"),
            "shaper_cpu_pct": _env_int_or_empty_as_none("COCK_MS_HOST_SHAPER_CPU_PCT"),
            "tc_qdisc_root": tc if tc else None,
        },
    }


def _write_from_env(db: Path) -> int:
    try:
        data = build_write_payload_from_environ()
    except (KeyError, ValueError) as e:
        print(f"conntrack-storage: write-from-env: {e}", file=sys.stderr)
        return 2
    data["database"] = str(db)
    try:
        run_write_from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        print(f"conntrack-storage: write-from-env: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"conntrack-storage: write-from-env: {e}", file=sys.stderr)
        return 1
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="conntrack_samples / host_samples SQLite access"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_m = sub.add_parser("migrate", help="Ensure schema and migrations")
    p_m.add_argument("--db", type=Path, required=True)

    p_r = sub.add_parser("read-last", help="Print last stats row (pipe-separated)")
    p_r.add_argument("--db", type=Path, required=True)

    p_w = sub.add_parser("write", help="Insert row + maintenance (JSON on stdin)")
    p_w.add_argument("--db", type=Path, required=True)

    p_we = sub.add_parser(
        "write-from-env",
        help="Insert row + maintenance (COCK_MS_* environment variables)",
    )
    p_we.add_argument("--db", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        try:
            return _migrate(args.db)
        except OSError as e:
            print(f"conntrack-storage: migrate: {e}", file=sys.stderr)
            return 1
    if args.cmd == "read-last":
        try:
            return _read_last(args.db)
        except OSError as e:
            print(f"conntrack-storage: read-last: {e}", file=sys.stderr)
            return 1
    if args.cmd == "write":
        return _write(args.db)
    if args.cmd == "write-from-env":
        return _write_from_env(args.db)
    return 2
