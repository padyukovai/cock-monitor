from __future__ import annotations

from pathlib import Path

import pytest
from cock_monitor.services.status_report import StatusReportError
from telegram_bot.status_provider import PythonStatusProvider


def test_python_status_provider_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("CHECK_CONNTRACK_FILL=0\n", encoding="utf-8")

    monkeypatch.setattr(
        "telegram_bot.status_provider.build_status_report",
        lambda _env_file: "ok body",
    )
    provider = PythonStatusProvider(env_file=env_file, timeout_sec=1)
    ok, body = provider.get_status()
    assert ok is True
    assert body == "ok body"


def test_python_status_provider_maps_domain_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("", encoding="utf-8")

    def _boom(_env_file: Path) -> str:
        raise StatusReportError("cannot build")

    monkeypatch.setattr("telegram_bot.status_provider.build_status_report", _boom)
    provider = PythonStatusProvider(env_file=env_file, timeout_sec=1)
    ok, body = provider.get_status()
    assert ok is False
    assert "cannot build" in body
