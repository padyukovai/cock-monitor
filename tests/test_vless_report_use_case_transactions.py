from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cock_monitor.services.vless_report_use_case import (
    VlessReportError,
    run_vless_report_use_case,
)


def _make_xui_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE client_traffics (
            email TEXT,
            up INTEGER,
            down INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE inbounds (
            protocol TEXT,
            settings TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO client_traffics (email, up, down) VALUES ('u1@example.com', 1000, 2000)"
    )
    conn.execute(
        """
        INSERT INTO inbounds (protocol, settings)
        VALUES ('vless', '{"clients":[{"email":"u1@example.com"}]}')
        """
    )
    conn.commit()
    conn.close()


def test_vless_use_case_rolls_back_all_writes_on_send_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "test.env"
    xui_db = tmp_path / "xui.db"
    metrics_db = tmp_path / "metrics.db"
    _make_xui_db(xui_db)
    env_file.write_text(
        "\n".join(
            [
                f"XUI_DB_PATH={xui_db}",
                f"METRICS_DB={metrics_db}",
                "VLESS_DAILY_TZ=Europe/Moscow",
                "TELEGRAM_BOT_TOKEN=t",
                "TELEGRAM_CHAT_ID=c",
            ]
        ),
        encoding="utf-8",
    )

    class _FailingClient:
        def __init__(self, _token: str) -> None:
            pass

        def send_message(self, _chat: str, _text: str, parse_mode: str | None = None) -> None:
            raise RuntimeError("telegram failed")

    monkeypatch.setattr(
        "cock_monitor.services.vless_report_use_case.TelegramClient",
        _FailingClient,
    )

    with pytest.raises(VlessReportError, match="telegram failed"):
        run_vless_report_use_case(
            env_file,
            mode="since-last-sent",
            send_telegram=True,
            dry_run=False,
        )

    conn = sqlite3.connect(str(metrics_db))
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "vless_daily_snapshots" in tables:
        assert conn.execute("SELECT COUNT(*) FROM vless_daily_snapshots").fetchone() == (0,)
    if "vless_report_checkpoints" in tables:
        assert conn.execute("SELECT COUNT(*) FROM vless_report_checkpoints").fetchone() == (0,)
    if "vless_daily_reports" in tables:
        assert conn.execute("SELECT COUNT(*) FROM vless_daily_reports").fetchone() == (0,)
    conn.close()


def test_vless_use_case_sends_photo_when_chart_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "test.env"
    xui_db = tmp_path / "xui.db"
    metrics_db = tmp_path / "metrics.db"
    _make_xui_db(xui_db)
    env_file.write_text(
        "\n".join(
            [
                f"XUI_DB_PATH={xui_db}",
                f"METRICS_DB={metrics_db}",
                "VLESS_DAILY_TZ=Europe/Moscow",
                "TELEGRAM_BOT_TOKEN=t",
                "TELEGRAM_CHAT_ID=c",
                "VLESS_CHART_ENABLE=1",
                "VLESS_CHART_TOP_N=5",
            ]
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    class _Client:
        def __init__(self, _token: str) -> None:
            pass

        def send_message(self, _chat: str, _text: str, parse_mode: str | None = None) -> None:
            calls.append("message")

        def send_photo(self, _chat: str, _photo_path: str | Path, *, caption: str = "") -> None:
            calls.append("photo")

    def _fake_generate(_rows: list[tuple[str, int]], out: Path, *, title: str) -> None:
        assert title
        out.write_bytes(b"png")

    monkeypatch.setattr(
        "cock_monitor.services.vless_report_use_case.TelegramClient",
        _Client,
    )
    monkeypatch.setattr(
        "cock_monitor.services.vless_report_use_case.generate_vless_top_chart",
        _fake_generate,
    )

    # first run creates baseline
    run_vless_report_use_case(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )
    # second run has prev_map and attempts chart send
    run_vless_report_use_case(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )
    assert calls.count("message") == 2
    assert calls.count("photo") == 1


def test_vless_use_case_chart_failure_falls_back_to_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "test.env"
    xui_db = tmp_path / "xui.db"
    metrics_db = tmp_path / "metrics.db"
    _make_xui_db(xui_db)
    env_file.write_text(
        "\n".join(
            [
                f"XUI_DB_PATH={xui_db}",
                f"METRICS_DB={metrics_db}",
                "VLESS_DAILY_TZ=Europe/Moscow",
                "TELEGRAM_BOT_TOKEN=t",
                "TELEGRAM_CHAT_ID=c",
                "VLESS_CHART_ENABLE=1",
            ]
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    class _Client:
        def __init__(self, _token: str) -> None:
            pass

        def send_message(self, _chat: str, _text: str, parse_mode: str | None = None) -> None:
            calls.append("message")

        def send_photo(self, _chat: str, _photo_path: str | Path, *, caption: str = "") -> None:
            calls.append("photo")

    def _raise_import_error(_rows: list[tuple[str, int]], out: Path, *, title: str) -> None:
        raise ImportError("matplotlib missing")

    monkeypatch.setattr(
        "cock_monitor.services.vless_report_use_case.TelegramClient",
        _Client,
    )
    monkeypatch.setattr(
        "cock_monitor.services.vless_report_use_case.generate_vless_top_chart",
        _raise_import_error,
    )

    run_vless_report_use_case(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )
    run_vless_report_use_case(
        env_file,
        mode="since-last-sent",
        send_telegram=True,
        dry_run=False,
    )
    assert calls.count("message") == 2
    assert calls.count("photo") == 0
