"""Incident sampler tick: collect probes, write JSONL, alert."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from cock_monitor.adapters.linux_host import read_hostname_fqdn, read_load_mem_from_proc
from cock_monitor.modules.incident.env import apply_incident_defaults, get_int, load_env_overwrite, resolve_env_file
from cock_monitor.modules.incident.level import compute_level, incident_hop_level_enabled
from cock_monitor.modules.incident.leak_profile import (
    append_leak_investigation_line,
    collect_leak_enriched,
    leak_investigation_enabled,
    leak_log_path,
    load_leak_state,
    maybe_finalize_leak_investigation,
)
from cock_monitor.modules.incident.postmortem import (
    build_json_line,
    incident_track_and_postmortem,
    maybe_alert,
    state_load,
    state_save,
)
from cock_monitor.modules.incident.probes import (
    collect_conntrack,
    collect_dns,
    collect_hop_links,
    collect_ping_groups,
    collect_ping_legacy,
    collect_tcp_probes,
    collect_tcp_states,
    collect_units,
)
from cock_monitor.platform.registry import module_enabled


def incident_enabled() -> bool:
    return module_enabled("incident", dict(os.environ))


def run_once() -> int:
    """Single incident tick (after env loaded)."""
    apply_incident_defaults()
    if not incident_enabled():
        return 0

    now_ts = int(time.time())
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    host = read_hostname_fqdn()
    log_dir = Path(os.environ.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor"))
    state_path = Path(os.environ.get("INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state"))

    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"incident-{time.strftime('%Y%m%d', time.gmtime(now_ts))}.jsonl"

    st = state_load(state_path)
    old_level = st.get("last_level", "OK")

    ping_count = get_int("INCIDENT_PING_COUNT", 2)
    ping_timeout = get_int("INCIDENT_PING_TIMEOUT_SEC", 1)
    ping_targets = os.environ.get("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8")

    ping_arr, ping_max_loss = collect_ping_legacy(ping_targets, ping_count, ping_timeout)
    ping_groups = collect_ping_groups()

    dns_host = os.environ.get("INCIDENT_DNS_HOST", "api.telegram.org")
    dns_timeout = get_int("INCIDENT_DNS_TIMEOUT_SEC", 2)
    dns_ok, dns_lat, dns_err = collect_dns(dns_host, dns_timeout)

    dns_streak = int(st.get("dns_fail_streak", "0") or "0")
    if dns_ok == 1:
        dns_streak = 0
    else:
        dns_streak += 1
    st["dns_fail_streak"] = str(dns_streak)

    ct_count, ct_max, ct_fill = collect_conntrack()
    tcp_states = collect_tcp_states()
    tcp_estab = tcp_states["estab"]
    tcp_syn = tcp_states["syn_recv"]
    tcp_tw = tcp_states["time_wait"]
    tcp_fin_wait = tcp_states["fin_wait"]
    tcp_close_wait = tcp_states["close_wait"]
    tcp_orphan = tcp_states["orphan"]
    hop_links = collect_hop_links()
    tcp_probe = collect_tcp_probes()

    load1, mem_kb = read_load_mem_from_proc()
    units = collect_units()

    tcp_en = int(tcp_probe.get("enabled", 0) or 0)
    tcp_total = int(tcp_probe.get("totals", {}).get("all", {}).get("total", 0) or 0)
    tcp_fails = int(tcp_probe.get("totals", {}).get("all", {}).get("fails", 0) or 0)
    tcp_warn = get_int("INCIDENT_TCP_PROBE_WARN_FAILS", 1)
    tcp_crit = get_int("INCIDENT_TCP_PROBE_CRIT_FAILS", 0)

    hop_level_links: list[dict[str, Any]] | None = None
    if incident_hop_level_enabled() and hop_links.get("enabled"):
        hop_level_links = hop_links.get("links")

    level = compute_level(
        fill_pct=ct_fill,
        conn_warn=get_int("INCIDENT_CONNTRACK_WARN_PCT", 85),
        conn_crit=get_int("INCIDENT_CONNTRACK_CRIT_PCT", 95),
        ping_max_loss=ping_max_loss,
        ping_loss_warn=get_int("INCIDENT_PING_LOSS_WARN_PCT", 20),
        dns_fail_streak=dns_streak,
        dns_streak_warn=get_int("INCIDENT_DNS_FAIL_STREAK_WARN", 3),
        tcp_enabled=tcp_en,
        tcp_fails=tcp_fails,
        tcp_warn_fail=tcp_warn,
        tcp_crit_fail=tcp_crit,
        tcp_fin_wait=tcp_fin_wait,
        tcp_fin_wait_warn=get_int("INCIDENT_TCP_FIN_WAIT_WARN", 0),
        tcp_close_wait=tcp_close_wait,
        tcp_close_wait_warn=get_int("INCIDENT_TCP_CLOSE_WAIT_WARN", 0),
        tcp_orphan=tcp_orphan,
        tcp_orphan_warn=get_int("INCIDENT_TCP_ORPHAN_WARN", 0),
        hop_links=hop_level_links,
        hop_estab_warn=get_int("INCIDENT_HOP_ESTAB_WARN", 5),
        hop_estab_crit=get_int("INCIDENT_HOP_ESTAB_CRIT", 20),
        hop_fin_wait_warn=get_int("INCIDENT_HOP_FIN_WAIT_WARN", 20),
        hop_fin_wait_crit=get_int("INCIDENT_HOP_FIN_WAIT_CRIT", 50),
    )

    line = build_json_line(
        ts_iso=ts_iso,
        ts_epoch=now_ts,
        host=host,
        level=level,
        ping=ping_arr,
        ping_groups=ping_groups,
        dns_host=dns_host,
        dns_ok=dns_ok,
        dns_lat=dns_lat,
        dns_err=dns_err,
        ct_count=ct_count,
        ct_max=ct_max,
        ct_fill=ct_fill,
        tcp_estab=tcp_estab,
        tcp_syn=tcp_syn,
        tcp_tw=tcp_tw,
        tcp_fin_wait=tcp_fin_wait,
        tcp_close_wait=tcp_close_wait,
        tcp_orphan=tcp_orphan,
        hop_links=hop_links,
        tcp_probe=tcp_probe,
        load1=load1,
        mem_kb=mem_kb,
        units=units,
    )

    with logfile.open("a", encoding="utf-8") as f:
        f.write(line)

    leak_st = load_leak_state()
    leak_active = leak_st.get("active") == "1" or leak_investigation_enabled()
    if leak_active:
        enriched = collect_leak_enriched(leak_st)
        leak_file = leak_log_path(now_ts)
        base_row = json.loads(line)
        with leak_file.open("a", encoding="utf-8") as f:
            f.write(append_leak_investigation_line(base_row, enriched))

    maybe_finalize_leak_investigation(host)

    incident_track_and_postmortem(old_level, level, now_ts, host, st, log_dir)

    hop_lines = ""
    if hop_links.get("enabled"):
        parts = []
        for link in hop_links.get("links", []):
            err = str(link.get("error") or "").strip()
            part = (
                f"{link.get('name')} estab={link.get('estab')} fin_wait={link.get('fin_wait')} "
                f"tw={link.get('time_wait')}"
            )
            if err:
                part += f" err={err}"
            parts.append(part)
        hop_lines = "\nhop: " + "; ".join(parts)

    snap = (
        f"incident {level} on {host}\n"
        f"time: {ts_iso}\n"
        f"conntrack: {ct_count}/{ct_max} ({ct_fill}%)\n"
        f"ping max loss: {ping_max_loss}%\n"
        f"dns: ok={dns_ok} streak={dns_streak} err={dns_err}\n"
        f"tcp: estab={tcp_estab} syn_recv={tcp_syn} tw={tcp_tw} "
        f"fin_wait={tcp_fin_wait} close_wait={tcp_close_wait} orphan={tcp_orphan}\n"
        f"tcp-probe all: {tcp_fails}/{tcp_total} failed\n"
        f"tcp-probe local: {tcp_probe['totals']['local']['fails']}/{tcp_probe['totals']['local']['total']} "
        f"target={tcp_probe['targets']['local']}\n"
        f"tcp-probe external: {tcp_probe['totals']['external']['fails']}/{tcp_probe['totals']['external']['total']} "
        f"target={tcp_probe['targets']['external']}"
        f"{hop_lines}"
    )
    maybe_alert(now_ts, level, st, snapshot_text=snap)

    st["last_level"] = level
    state_save(state_path, st)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 1 and argv[0] in ("-h", "--help"):
        print("Usage: python -m cock_monitor run incident /path/to.env", file=sys.stderr)
        return 2
    env_path = resolve_env_file(argv[0] if argv else None)
    if env_path is None:
        print("incident: missing env file path", file=sys.stderr)
        return 2
    if not env_path.is_file():
        print(f"incident: config not found: {env_path}", file=sys.stderr)
        return 1
    load_env_overwrite(env_path)
    try:
        return run_once()
    except OSError as e:
        print(f"incident: {e}", file=sys.stderr)
        return 1
