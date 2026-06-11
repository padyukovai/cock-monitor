"""Tests for burst capture helpers."""
from __future__ import annotations

import json
from pathlib import Path

from cock_monitor.adapters import burst_access_log as bal
from cock_monitor.adapters import linux_host as lh
from cock_monitor.services import burst_capture, burst_report

SS_SUMMARY = """Total: 200
TCP:   140 (estab 9, closed 120, orphaned 18, timewait 8)
Transport Total     IP
"""


NETSTAT = """TcpExt: SyncookiesSent SyncookiesRecv SyncookiesFailed ListenOverflows ListenDrops TCPTimeouts
TcpExt: 0 0 0 2 0 5
"""


def test_parse_ss_summary() -> None:
    out = lh.parse_ss_summary(SS_SUMMARY)
    assert out["estab"] == 9
    assert out["orphan"] == 18
    assert out["syn_recv"] == 0
    assert out["timewait"] == 8


def test_parse_ss_port_state_counts() -> None:
    out = """ESTAB 0 0 *:443 1.2.3.4:12345
SYN-RECV 0 0 *:443 5.6.7.8:9999
SYN-RECV 0 0 *:443 5.6.7.9:9998
"""
    counts = lh.parse_ss_port_state_counts(out)
    assert counts["estab"] == 1
    assert counts["syn_recv"] == 2


def test_parse_netstat_tcp_ext() -> None:
    out = lh.parse_netstat_tcp_ext(NETSTAT, ("ListenOverflows", "TCPTimeouts"))
    assert out["ListenOverflows"] == 2
    assert out["TCPTimeouts"] == 5


def test_read_sockstat_tcp(tmp_path: Path) -> None:
    p = tmp_path / "sockstat"
    p.write_text("TCP: inuse 10 orphan 3 tw 2 alloc 1 mem 0\n", encoding="utf-8")
    out = lh.read_sockstat_tcp(p)
    assert out["inuse"] == 10
    assert out["orphan"] == 3
    assert out["tw"] == 2


def test_seek_log_to_end_skips_existing(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    log.write_text("old accepted line\n", encoding="utf-8")
    state = bal.LogTailState(path=log)
    bal.seek_log_to_end(state)
    tracker = bal.BurstLogTracker(access=state)
    d0 = tracker.poll_access()
    assert d0.delta_lines == 0 and d0.delta_accepted == 0
    log.write_text(log.read_text(encoding="utf-8") + "2026/01/01 from 1.1.1.1:1 accepted tcp:x:443 email:t\n", encoding="utf-8")
    d1 = tracker.poll_access()
    assert d1.delta_accepted == 1


def test_burst_access_log_delta(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    log.write_text(
        "2026/01/01 10:00:00 from 1.2.3.4:1111 accepted tcp:api.ipify.org:443 email:test\n",
        encoding="utf-8",
    )
    tracker = bal.BurstLogTracker(access=bal.LogTailState(path=log))
    d1 = tracker.poll_access(client_ip="1.2.3.4")
    assert d1.delta_lines == 1
    assert d1.delta_accepted == 1
    assert d1.delta_from_ip == 1
    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026/01/01 10:00:01 from 9.9.9.9:2222 accepted tcp:api.ipify.org:443 email:other\n",
        encoding="utf-8",
    )
    d2 = tracker.poll_access(client_ip="1.2.3.4")
    assert d2.delta_accepted == 1
    assert d2.delta_from_ip == 0


def test_burst_report_handshake_stall(tmp_path: Path) -> None:
    rows = [
        {
            "ts_epoch": 1,
            "host": "cock-london",
            "sampler": "burst-capture",
            "port443": {"estab": 9, "syn_recv": 2},
            "ss": {"orphan": 18, "estab": 140},
            "conntrack": {"fill_pct": 1},
            "access_log": {"delta_accepted": 0},
            "netstat": {"ListenOverflows": 0},
            "xray": {"cpu_pct": 2.0, "fds": 10},
        }
    ]
    path = tmp_path / "burst.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    samples = burst_report.load_burst_samples(path)
    agg = burst_report.aggregate_samples(samples)
    verdict, _ = burst_report.compute_verdict(agg)
    assert verdict == "handshake_stall"


def test_burst_report_ok(tmp_path: Path) -> None:
    rows = [
        {
            "ts_epoch": 1,
            "host": "cock-is",
            "sampler": "burst-capture",
            "port443": {"estab": 8, "syn_recv": 0},
            "ss": {"orphan": 2},
            "conntrack": {"fill_pct": 1},
            "access_log": {"delta_accepted": 8},
            "netstat": {"ListenOverflows": 0},
            "xray": {"cpu_pct": 5.0, "fds": 20},
        }
    ]
    path = tmp_path / "burst-ok.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    agg = burst_report.aggregate_samples(burst_report.load_burst_samples(path))
    verdict, _ = burst_report.compute_verdict(agg)
    assert verdict == "ok"


def test_burst_state_roundtrip(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "burst.state"
    monkeypatch.setenv("BURST_STATE_FILE", str(state_file))
    burst_capture.apply_burst_defaults()
    burst_capture.save_state(1234, tmp_path / "burst.jsonl", 100)
    st = burst_capture.load_state()
    assert st["pid"] == "1234"
    assert st["started_at"] == "100"
