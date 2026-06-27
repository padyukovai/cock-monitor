"""Tests for WireGuard collector."""

from __future__ import annotations

from cock_monitor.modules.wg.collector import WgPeer, WgSnapshot, format_status


def test_wg_snapshot_stale_count() -> None:
    snap = WgSnapshot(
        interface="wg0",
        peers=(
            WgPeer("abc…", "1.2.3.4:51820", 500, 1000, 2000),
            WgPeer("def…", "?", None, 0, 0),
        ),
    )
    assert snap.peer_count == 2
    assert snap.stale_count(180) == 2
    text = format_status(snap, stale_sec=180, top_n=2)
    assert "WireGuard wg0" in text
    assert "stale handshake" in text
