"""Parse `wg show` output."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class WgPeer:
    public_key: str
    endpoint: str
    latest_handshake_sec: int | None
    transfer_rx: int
    transfer_tx: int


@dataclass(frozen=True)
class WgSnapshot:
    interface: str
    peers: tuple[WgPeer, ...]

    @property
    def peer_count(self) -> int:
        return len(self.peers)

    @property
    def total_rx(self) -> int:
        return sum(p.transfer_rx for p in self.peers)

    @property
    def total_tx(self) -> int:
        return sum(p.transfer_tx for p in self.peers)

    def stale_count(self, max_age_sec: int) -> int:
        n = 0
        for p in self.peers:
            if p.latest_handshake_sec is None:
                n += 1
            elif p.latest_handshake_sec > max_age_sec:
                n += 1
        return n


def _parse_handshake_age(token: str) -> int | None:
    token = token.strip()
    if not token or token in {"0", "(none)"}:
        return None
    if token.endswith("s") and token[:-1].isdigit():
        return int(token[:-1])
    if token.isdigit():
        return int(token)
    # e.g. "1 minute, 23 seconds" — approximate via first number
    parts = token.replace(",", "").split()
    total = 0
    i = 0
    while i < len(parts):
        if parts[i].isdigit():
            val = int(parts[i])
            unit = parts[i + 1] if i + 1 < len(parts) else "seconds"
            if unit.startswith("minute"):
                total += val * 60
            elif unit.startswith("hour"):
                total += val * 3600
            elif unit.startswith("day"):
                total += val * 86400
            else:
                total += val
            i += 2
        else:
            i += 1
    return total if total else None


def collect_wg_snapshot(interface: str) -> WgSnapshot | None:
    try:
        proc = subprocess.run(
            ["wg", "show", interface, "dump"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None

    peers: list[WgPeer] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        # dump format: pubkey psk endpoint allowed_ips latest_handshake rx tx persistent_keepalive
        pubkey = parts[0][:12] + "…"
        endpoint = parts[2] if parts[2] != "(none)" else "?"
        hs_raw = parts[4]
        hs = int(hs_raw) if hs_raw.isdigit() else None
        if hs is not None and hs > 0:
            import time

            age = max(0, int(time.time()) - hs)
        else:
            age = None
        try:
            rx = int(parts[5])
            tx = int(parts[6])
        except ValueError:
            rx = tx = 0
        peers.append(
            WgPeer(
                public_key=pubkey,
                endpoint=endpoint,
                latest_handshake_sec=age,
                transfer_rx=rx,
                transfer_tx=tx,
            )
        )
    return WgSnapshot(interface=interface, peers=tuple(peers))


def format_status(snapshot: WgSnapshot, *, stale_sec: int, top_n: int = 5) -> str:
    lines = [
        f"WireGuard {snapshot.interface}: {snapshot.peer_count} peers",
        f"total rx/tx: {snapshot.total_rx} / {snapshot.total_tx} bytes",
        f"stale handshake (>{stale_sec}s): {snapshot.stale_count(stale_sec)}",
    ]
    ranked = sorted(snapshot.peers, key=lambda p: p.transfer_rx + p.transfer_tx, reverse=True)
    for p in ranked[:top_n]:
        hs = f"{p.latest_handshake_sec}s ago" if p.latest_handshake_sec is not None else "never"
        lines.append(f"  {p.public_key} @ {p.endpoint} hs={hs} rx={p.transfer_rx} tx={p.transfer_tx}")
    return "\n".join(lines)
