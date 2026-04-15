"""Unit tests for MTProxy collector parsing (ss / iptables stdout)."""

from __future__ import annotations

from mtproxy_module.collector import parse_iptables_monitor_stdout, parse_ss_stdout


def test_parse_ss_stdout_ipv4_and_ipv6() -> None:
    stdout = """Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
ESTAB 0      0      10.0.0.1:8443             203.0.113.5:12345
ESTAB 0      0      10.0.0.1:8443             [2001:db8::1]:54321
ESTAB 0      0      10.0.0.1:8443             198.51.100.2:9999
"""
    got = parse_ss_stdout(stdout)
    assert got["total"] == 3
    assert got["unique_ips"] == 3
    assert got["per_ip"]["203.0.113.5"] == 1
    assert got["per_ip"]["2001:db8::1"] == 1
    assert got["per_ip"]["198.51.100.2"] == 1


def test_parse_iptables_monitor_dpt_spt() -> None:
    stdout = """Chain MTPROXY_MONITOR (2 references)
 pkts bytes target     prot opt in     out     source               destination
    0  1500            0    --  *      *       0.0.0.0/0            0.0.0.0/0            dpt:8443
    0  3200            0    --  *      *       0.0.0.0/0            0.0.0.0/0            spt:8443
"""
    current_in, current_out = parse_iptables_monitor_stdout(stdout, 8443)
    assert current_in == 1500
    assert current_out == 3200


def test_parse_iptables_monitor_other_port_ignored() -> None:
    stdout = """Chain MTPROXY_MONITOR (2 references)
 pkts bytes target     prot opt in     out     source               destination
    0  100            0    --  *      *       0.0.0.0/0            0.0.0.0/0            dpt:443
    0  5000            0    --  *      *       0.0.0.0/0            0.0.0.0/0            dpt:8443
"""
    current_in, current_out = parse_iptables_monitor_stdout(stdout, 8443)
    assert current_in == 5000
    assert current_out == 0
