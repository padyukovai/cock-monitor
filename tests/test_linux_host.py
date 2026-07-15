"""Tests for cock_monitor.adapters.linux_host."""
from __future__ import annotations

import pytest

from pathlib import Path

from cock_monitor.adapters import linux_host as lh


def test_parse_loadavg_first_field() -> None:
    assert lh.parse_loadavg_first_field("0.52 0.48 0.41 1/200 1234") == "0.52"
    assert lh.parse_loadavg_first_field("") is None


def test_parse_memavailable_kb() -> None:
    text = "MemTotal:       8000000 kB\nMemAvailable:   1234567 kB\n"
    assert lh.parse_memavailable_kb(text) == 1234567


def test_parse_ss_tan_state_counts() -> None:
    out = """State  Recv-Q Send-Q Local Address:Port Peer Address:PortProcess
ESTAB  0      0      1.1.1.1:443        2.2.2.2:12345
ESTAB  0      0      1.1.1.1:80         3.3.3.3:23456
SYN-RECV 0    0      0.0.0.0:443        0.0.0.0:*
TIME-WAIT 0    0      1.1.1.1:443        2.2.2.2:9999
FIN-WAIT-1 0  0      1.1.1.1:10089      2.2.2.2:11111
CLOSE-WAIT 0  0      1.1.1.1:10089      2.2.2.2:22222
"""
    e, s, t = lh.parse_ss_tan_state_counts(out)
    assert e == 2 and s == 1 and t == 1
    ext = lh.parse_ss_tan_extended_counts(out)
    assert ext["fin_wait"] == 1
    assert ext["close_wait"] == 1


def test_parse_ss_port_state_counts_includes_fin_wait() -> None:
    out = "FIN-WAIT-2 0 0 1.1.1.1:10089 2.2.2.2:443\nESTAB 0 0 1.1.1.1:10089 2.2.2.2:444\n"
    counts = lh.parse_ss_port_state_counts(out)
    assert counts["estab"] == 1
    assert counts["fin_wait"] == 1


def test_safe_pct() -> None:
    assert lh.safe_pct(50, 100) == 50
    assert lh.safe_pct(1, 0) == 0


def test_read_load_mem_from_proc(tmp_path: Path) -> None:
    la = tmp_path / "loadavg"
    mi = tmp_path / "meminfo"
    la.write_text("1.25 0 0 0/0 0\n", encoding="utf-8")
    mi.write_text("MemAvailable:   999 kB\n", encoding="utf-8")
    l1, kb = lh.read_load_mem_from_proc(la, mi)
    assert l1 == "1.25"
    assert kb == 999


def test_find_main_xray_pid_prefers_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cock_monitor.adapters import linux_host as lh

    # fake PIDs: hop-probe (newest), hop-src, main
    fake = {
        101: ("/usr/local/x-ui/bin/xray-linux-amd64 run -c /etc/xray-hop-probe/config.json", 5000, 1),
        102: ("/usr/local/x-ui/bin/xray-linux-amd64 run -c /tmp/hop-src-185.189.14.152.json", 4000, 1),
        103: ("bin/xray-linux-amd64 -c bin/config.json", 600_000, 50),
    }

    monkeypatch.setattr(lh, "find_processes_by_cmdline", lambda pattern: [101, 102, 103])
    monkeypatch.setattr(lh, "_is_xray_binary", lambda pid: True)
    monkeypatch.setattr(lh, "_read_proc_cmdline", lambda pid: fake[pid][0])
    monkeypatch.setattr(lh, "_read_proc_rss_kb", lambda pid: fake[pid][1])
    monkeypatch.setattr(lh, "_parent_is_xui", lambda pid: fake[pid][2] == 50)
    monkeypatch.setattr(lh, "find_process_by_comm", lambda pattern: 101)

    assert lh.find_main_xray_pid("xray-linux-amd64") == 103


def test_find_main_xray_pid_falls_back_to_max_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    from cock_monitor.adapters import linux_host as lh

    fake = {
        201: ("/usr/local/x-ui/bin/xray-linux-amd64 -c /opt/custom.json", 12_000, False),
        202: ("/usr/local/x-ui/bin/xray-linux-amd64 -c /opt/other.json", 400_000, False),
    }
    monkeypatch.setattr(lh, "find_processes_by_cmdline", lambda pattern: [201, 202])
    monkeypatch.setattr(lh, "_is_xray_binary", lambda pid: True)
    monkeypatch.setattr(lh, "_read_proc_cmdline", lambda pid: fake[pid][0])
    monkeypatch.setattr(lh, "_read_proc_rss_kb", lambda pid: fake[pid][1])
    monkeypatch.setattr(lh, "_parent_is_xui", lambda pid: fake[pid][2])
    # no bin/config.json → include_hit=0 for both → pool=scored → max RSS
    assert lh.find_main_xray_pid(
        "xray-linux-amd64",
        cmdline_include=("bin/config.json",),
        cmdline_exclude=("xray-hop-probe",),
    ) == 202
