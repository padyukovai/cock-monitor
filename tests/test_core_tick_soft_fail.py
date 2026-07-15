"""Core tick must still run leak_watchdog if mem_alert Telegram fails."""

from __future__ import annotations

from pathlib import Path

import pytest

from cock_monitor.modules.core import service as core_service


def test_core_tick_runs_watchdog_after_mem_alert_soft_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / "env"
    env.write_text("ENABLED_MODULES=core\n", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(
        core_service,
        "run_conntrack_check",
        lambda env_file, dry_run_override=False: calls.append("ct") or 0,
    )
    monkeypatch.setattr(
        core_service,
        "run_mem_alert",
        lambda env_file, dry_run=False: calls.append("mem") or 1,
    )
    monkeypatch.setattr(
        core_service,
        "run_leak_alert",
        lambda env_file, dry_run=False: calls.append("leak") or 0,
    )
    monkeypatch.setattr(
        core_service,
        "run_leak_watchdog",
        lambda env_file, dry_run=False: calls.append("wd") or 0,
    )

    assert core_service.run_core_tick(env) == 1
    assert calls == ["ct", "mem", "leak", "wd"]
