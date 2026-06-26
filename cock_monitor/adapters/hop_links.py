"""Collect TCP state counts for configured VLESS hop links via ss."""
from __future__ import annotations

import subprocess
from typing import Any

from cock_monitor.adapters.linux_host import parse_ss_state_line_counts


def parse_hop_link_spec(spec: str) -> dict[str, Any] | None:
    """Parse hop link spec: name:dst:host:port or name:sport::port."""
    raw = spec.strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) == 4 and parts[1] == "dst":
        name, _, host, port_s = parts
        if not name or not host:
            return None
        try:
            port = int(port_s)
        except ValueError:
            return None
        return {"name": name, "mode": "dst", "host": host, "port": port}
    if len(parts) == 4 and parts[1] == "sport":
        name, _, _host, port_s = parts
        if not name:
            return None
        try:
            port = int(port_s)
        except ValueError:
            return None
        return {"name": name, "mode": "sport", "host": "", "port": port}
    return None


def parse_hop_links_env(raw: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for chunk in raw.replace("\n", ",").split(","):
        spec = parse_hop_link_spec(chunk)
        if spec is not None:
            links.append(spec)
    return links


def hop_ss_args(link: dict[str, Any]) -> list[str]:
    mode = str(link.get("mode", ""))
    port = int(link.get("port", 0) or 0)
    if mode == "dst":
        host = str(link.get("host", "")).strip()
        return ["ss", "-Htan", f"dst {host}:{port}"]
    if mode == "sport":
        return ["ss", "-Htan", f"sport = :{port}"]
    return []


def _empty_states() -> dict[str, int]:
    return {
        "estab": 0,
        "syn_recv": 0,
        "time_wait": 0,
        "fin_wait": 0,
        "close_wait": 0,
    }


def collect_hop_link_states(spec: dict[str, Any]) -> dict[str, Any]:
    args = hop_ss_args(spec)
    states = _empty_states()
    error = ""
    if not args:
        error = "invalid_spec"
    else:
        try:
            out = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if out.returncode != 0:
                error = f"ss_rc_{out.returncode}"
            elif out.stdout:
                states = parse_ss_state_line_counts(out.stdout)
        except (OSError, subprocess.SubprocessError) as e:
            error = f"ss_failed_{getattr(e, 'errno', -1)}"
    return {
        "name": spec["name"],
        "mode": spec["mode"],
        "host": spec.get("host", ""),
        "port": spec["port"],
        "estab": states["estab"],
        "syn_recv": states["syn_recv"],
        "time_wait": states["time_wait"],
        "fin_wait": states["fin_wait"],
        "close_wait": states["close_wait"],
        "error": error,
    }


def collect_hop_links(links_raw: str) -> dict[str, Any]:
    specs = parse_hop_links_env(links_raw.strip())
    if not specs:
        return {"enabled": 0, "links": []}
    links = [collect_hop_link_states(spec) for spec in specs]
    return {"enabled": 1, "links": links}
