#!/usr/bin/env python3
"""Build HTML post-mortem summary from incident JSONL samples (stdin or files)."""
from __future__ import annotations

import glob
import html
import json
import os
import sys
from datetime import datetime, timezone


def _iso_from_epoch(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _max_ping_loss(ping: object) -> int:
    if not isinstance(ping, list):
        return 0
    m = 0
    for p in ping:
        if isinstance(p, dict) and isinstance(p.get("loss_pct"), (int, float)):
            m = max(m, int(p["loss_pct"]))
    return m


def _load_samples(log_dir: str, start_ts: int, end_ts: int, host: str | None) -> list[dict]:
    samples: list[dict] = []
    pattern = os.path.join(log_dir, "incident-*.jsonl")
    paths = sorted(glob.glob(pattern))
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("sampler") != "incident-sampler":
                        continue
                    if host and row.get("host") != host:
                        continue
                    te = row.get("ts_epoch")
                    if not isinstance(te, int):
                        continue
                    if start_ts <= te <= end_ts:
                        samples.append(row)
        except OSError:
            continue
    samples.sort(key=lambda r: int(r.get("ts_epoch", 0)))
    return samples


def build_html(
    host: str,
    peak_level: str,
    start_ts: int,
    end_ts: int,
    samples: list[dict],
) -> str:
    if not samples:
        return (
            f"<b>Post-mortem</b> <code>{html.escape(host)}</code>\n"
            f"Peak: <b>{html.escape(peak_level)}</b>\n"
            f"Window: <code>{_iso_from_epoch(start_ts)}</code> … <code>{_iso_from_epoch(end_ts)}</code>\n"
            "<i>No JSONL samples in window (log missing or rotated).</i>"
        )

    dur = max(0, end_ts - start_ts)
    m, s = divmod(dur, 60)
    h, m = divmod(m, 60)
    if h:
        dur_s = f"{h}h {m}m {s}s"
    elif m:
        dur_s = f"{m}m {s}s"
    else:
        dur_s = f"{s}s"

    dns_fail = 0
    dns_ok = 0
    dns_lat_max = 0
    max_streak = 0
    cur_streak = 0
    max_fill = 0
    max_syn = 0
    max_estab = 0
    max_tw = 0
    max_loss = 0
    max_load = 0.0
    min_mem = None
    first_bad = None
    last_bad = None
    levels_non_ok = 0

    for row in samples:
        lvl = row.get("level")
        if lvl != "OK":
            levels_non_ok += 1
            te = int(row["ts_epoch"])
            if first_bad is None:
                first_bad = te
            last_bad = te

        dns = row.get("dns") or {}
        if dns.get("ok") == 1:
            dns_ok += 1
            cur_streak = 0
        else:
            dns_fail += 1
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        lat = dns.get("latency_ms")
        if isinstance(lat, int) and lat >= 0:
            dns_lat_max = max(dns_lat_max, lat)

        ct = row.get("conntrack") or {}
        fp = ct.get("fill_pct")
        if isinstance(fp, int):
            max_fill = max(max_fill, fp)

        tcp = row.get("tcp") or {}
        v = tcp.get("syn_recv")
        if isinstance(v, int):
            max_syn = max(max_syn, v)
        v = tcp.get("estab")
        if isinstance(v, int):
            max_estab = max(max_estab, v)
        v = tcp.get("time_wait")
        if isinstance(v, int):
            max_tw = max(max_tw, v)

        max_loss = max(max_loss, _max_ping_loss(row.get("ping")))

        la = row.get("load1")
        if isinstance(la, (int, float)):
            max_load = max(max_load, float(la))

        mem = row.get("mem_avail_kb")
        if isinstance(mem, int):
            min_mem = mem if min_mem is None else min(min_mem, mem)

    first_ts = int(samples[0]["ts_epoch"])
    last_ts = int(samples[-1]["ts_epoch"])
    n = len(samples)

    lines = [
        f"<b>Post-mortem: сеть восстановлена</b>",
        f"Host: <code>{html.escape(host)}</code>",
        f"Peak: <b>{html.escape(peak_level)}</b> · samples in incident: <code>{levels_non_ok}</code> / <code>{n}</code>",
        f"Окно: <code>{_iso_from_epoch(start_ts)}</code> → <code>{_iso_from_epoch(end_ts)}</code> (~{html.escape(dur_s)})",
        f"Первая строка лога: <code>{_iso_from_epoch(first_ts)}</code> · последняя: <code>{_iso_from_epoch(last_ts)}</code>",
    ]
    if first_bad is not None and last_bad is not None:
        lines.append(
            f"Уровень ≠OK: с <code>{_iso_from_epoch(first_bad)}</code> по <code>{_iso_from_epoch(last_bad)}</code>"
        )

    lines.append("")
    lines.append("<b>DNS</b>")
    lines.append(
        f"ok/fail: <code>{dns_ok}</code>/<code>{dns_fail}</code> · max streak fail: <code>{max_streak}</code> · max latency ms: <code>{dns_lat_max}</code>"
    )

    lines.append("")
    lines.append("<b>Ping</b> (max loss % по целям)")
    lines.append(f"<code>{max_loss}</code>")

    lines.append("")
    lines.append("<b>Conntrack / TCP</b>")
    lines.append(
        f"max fill %: <code>{max_fill}</code> · max estab/syn_recv/tw: <code>{max_estab}</code>/<code>{max_syn}</code>/<code>{max_tw}</code>"
    )

    lines.append("")
    lines.append("<b>Host</b>")
    mem_s = str(min_mem) if min_mem is not None else "n/a"
    lines.append(f"max load1: <code>{max_load:.2f}</code> · min MemAvailable kB: <code>{mem_s}</code>")

    text = "\n".join(lines)
    # Telegram hard limit ~4096; keep margin
    if len(text) > 3800:
        text = text[:3790] + "\n…</i>"
    return text


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "Usage: incident-postmortem.py START_EPOCH END_EPOCH LOG_DIR HOST [PEAK_LEVEL]",
            file=sys.stderr,
        )
        return 2
    start_ts = int(sys.argv[1])
    end_ts = int(sys.argv[2])
    log_dir = sys.argv[3]
    host = sys.argv[4]
    peak = sys.argv[5] if len(sys.argv) > 5 else "WARN"
    samples = _load_samples(log_dir, start_ts, end_ts, host)
    out = build_html(host, peak, start_ts, end_ts, samples)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
