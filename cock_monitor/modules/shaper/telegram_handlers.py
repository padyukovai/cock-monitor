"""Shaper module tick + Telegram handlers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cock_monitor.platform.telegram.handler_utils import (
    TelegramHandlerContext,
    run_command_with_timeout,
    upsert_env_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def run_shaper_tick(env_file: Path, *, dry_run: bool = False) -> int:
    script = _REPO_ROOT / "bin" / "cock-cpu-shaper.sh"
    args = [str(script)]
    if dry_run:
        args.append("--dry-run")
    args.append(str(env_file))
    return subprocess.run(args, check=False).returncode


def _apply_global_cake_limit(*, iface: str, rate_mbit: int) -> str:
    cmd = [
        "tc",
        "qdisc",
        "replace",
        "dev",
        iface,
        "root",
        "cake",
        "bandwidth",
        f"{rate_mbit}mbit",
        "flowblind",
        "dual-dsthost",
    ]
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if out.returncode != 0:
        err = (out.stderr or out.stdout or "unknown tc error").strip()
        raise RuntimeError(f"tc apply failed on {iface}: {err}")
    return f"Applied global CAKE limit on {iface}: {rate_mbit}M"


def handle_cake_bw(ctx: TelegramHandlerContext) -> None:
    parts = ctx.text.split()
    if len(parts) not in (2, 3):
        ctx.client.send_message(ctx.chat_id, "Usage: /cake_bw <mbit> [--force]")
        return
    force_mode = len(parts) == 3 and parts[2].strip().lower() == "--force"
    if len(parts) == 3 and not force_mode:
        ctx.client.send_message(ctx.chat_id, "Unknown flag. Usage: /cake_bw <mbit> [--force]")
        return
    try:
        new_max_rate = int(parts[1])
    except ValueError:
        ctx.client.send_message(ctx.chat_id, "Invalid value. Must be integer Mbit.")
        return
    if new_max_rate <= 0:
        ctx.client.send_message(ctx.chat_id, "Invalid value. Must be > 0.")
        return
    iface = ctx.raw_env.get("SHAPER_IFACE", "ens3").strip() or "ens3"
    min_rate = int(ctx.raw_env.get("SHAPER_MIN_RATE_MBIT", "10") or "10")
    if new_max_rate < min_rate:
        ctx.client.send_message(
            ctx.chat_id,
            f"Rejected: {new_max_rate}M is below SHAPER_MIN_RATE_MBIT={min_rate}M.",
        )
        return
    upsert_env_key(ctx.env_file, "SHAPER_MAX_RATE_MBIT", str(new_max_rate))
    if force_mode:
        ok, msg = run_command_with_timeout(
            ctx.client,
            ctx.chat_id,
            "cake_bw --force",
            lambda: _apply_global_cake_limit(iface=iface, rate_mbit=new_max_rate),
        )
        if ok and isinstance(msg, str):
            ctx.client.send_message(
                ctx.chat_id,
                f"Updated SHAPER_MAX_RATE_MBIT={new_max_rate}M.\n{msg}",
            )
        return
    ctx.client.send_message(
        ctx.chat_id,
        f"Updated SHAPER_MAX_RATE_MBIT={new_max_rate}M. Shaper timer will apply on next run.",
    )
