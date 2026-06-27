"""Read cumulative outbound traffic counters from the local Xray Stats API."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from cock_monitor.adapters.xui_sqlite import OutboundTrafficRow, safe_i64

_OUTBOUND_STAT_RE = re.compile(r"^outbound>>>(.+?)>>>traffic>>>(uplink|downlink)$")


@dataclass(frozen=True)
class XrayStatsQueryResult:
    rows: list[OutboundTrafficRow]
    error: str = ""


def query_outbound_traffic_stats(
    *,
    api_addr: str,
    xray_bin: str,
    timeout_sec: int = 10,
) -> XrayStatsQueryResult:
    """Query Xray statsquery for outbound>>>tag>>>traffic>>>uplink/downlink counters."""
    api_addr = api_addr.strip()
    xray_bin = xray_bin.strip()
    if not api_addr or not xray_bin:
        return XrayStatsQueryResult(rows=[], error="missing_api_or_bin")

    try:
        proc = subprocess.run(
            [xray_bin, "api", "statsquery", f"--server={api_addr}"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return XrayStatsQueryResult(rows=[], error=f"statsquery_failed:{exc}")

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return XrayStatsQueryResult(
            rows=[],
            error=f"statsquery_rc_{proc.returncode}:{err[:200]}",
        )

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return XrayStatsQueryResult(rows=[], error="statsquery_invalid_json")

    stats = payload.get("stat")
    if not isinstance(stats, list):
        return XrayStatsQueryResult(rows=[])

    by_tag: dict[str, dict[str, int]] = {}
    for item in stats:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        match = _OUTBOUND_STAT_RE.match(name)
        if not match:
            continue
        tag, direction = match.group(1), match.group(2)
        counters = by_tag.setdefault(tag, {"up": 0, "down": 0})
        value = safe_i64(item.get("value"))
        if direction == "uplink":
            counters["up"] = value
        else:
            counters["down"] = value

    rows = [
        OutboundTrafficRow(tag=tag, up=vals["up"], down=vals["down"])
        for tag, vals in sorted(by_tag.items())
    ]
    return XrayStatsQueryResult(rows=rows)
