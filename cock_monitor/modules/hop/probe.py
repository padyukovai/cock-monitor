"""Parallel egress probe through local SOCKS proxy (optional)."""

from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HopProbeSpec:
    name: str
    proxy: str
    url: str
    expect_substr: str


def parse_hop_probe_spec(spec: str) -> HopProbeSpec | None:
    """Parse name:proxy:url:expect_substr (url may contain colons)."""
    raw = spec.strip()
    if not raw:
        return None
    name_end = raw.find(":")
    if name_end <= 0:
        return None
    name = raw[:name_end]
    rest = raw[name_end + 1 :]
    url_start = -1
    for scheme in ("https://", "http://"):
        pos = rest.find(scheme)
        if pos >= 0:
            url_start = pos
            break
    if url_start < 0:
        return None
    proxy = rest[:url_start].rstrip(":")
    url_and_expect = rest[url_start:]
    last_colon = url_and_expect.rfind(":")
    if last_colon <= 8:
        return None
    url = url_and_expect[:last_colon]
    expect = url_and_expect[last_colon + 1 :]
    if not name or not proxy or not url or not expect:
        return None
    return HopProbeSpec(name=name, proxy=proxy, url=url, expect_substr=expect)


def parse_hop_probes_env(raw: str) -> list[HopProbeSpec]:
    specs: list[HopProbeSpec] = []
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        spec = parse_hop_probe_spec(chunk)
        if spec is not None:
            specs.append(spec)
    return specs


def _curl_once(proxy: str, url: str, timeout_sec: int, expect_substr: str) -> tuple[bool, int]:
    t0 = time.time_ns()
    ok = False
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                str(timeout_sec),
                "--proxy",
                proxy,
                "-w",
                "\n%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 5,
            check=False,
        )
        stdout = proc.stdout or ""
        lines = stdout.rsplit("\n", 1)
        body = lines[0] if lines else ""
        http_code = 0
        if len(lines) == 2 and lines[1].isdigit():
            http_code = int(lines[1])
        ok = proc.returncode == 0 and http_code == 200 and expect_substr in body
    except (OSError, subprocess.SubprocessError):
        ok = False
    lat_ms = max(0, (time.time_ns() - t0) // 1_000_000)
    return ok, lat_ms


def run_hop_probe(
    spec: HopProbeSpec,
    *,
    parallel: int,
    timeout_sec: int,
) -> dict[str, Any]:
    parallel = max(1, parallel)
    results: list[tuple[bool, int]] = []

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [
            pool.submit(_curl_once, spec.proxy, spec.url, timeout_sec, spec.expect_substr)
            for _ in range(parallel)
        ]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                results.append((False, 0))

    oks = sum(1 for ok, _ in results if ok)
    lats = sorted(lat for _, lat in results)
    p50 = lats[len(lats) // 2] if lats else 0
    total = len(results)
    return {
        "name": spec.name,
        "proxy": spec.proxy,
        "url": spec.url,
        "expect": spec.expect_substr,
        "parallel": parallel,
        "ok": oks,
        "total": total,
        "success_pct": (oks * 100 // total) if total else 0,
        "latency_p50_ms": p50,
        "error": "" if oks == total else "partial_or_total_failure",
    }
