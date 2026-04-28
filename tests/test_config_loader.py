from __future__ import annotations

from pathlib import Path

from cock_monitor.config_loader import load_config


def test_load_config_parses_defaults_and_sections(tmp_path: Path) -> None:
    env_path = tmp_path / "cfg.env"
    env_path.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=test-token",
                "TELEGRAM_CHAT_ID=123",
                "WARN_PERCENT=81",
                "CRIT_PERCENT=96",
                "MTPROXY_ENABLE=1",
                "MTPROXY_PORT=9443",
                "VLESS_DAILY_TOP_N=15",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_config(env_path)
    assert loaded.app.telegram.bot_token == "test-token"
    assert loaded.app.conntrack.warn_percent == 81
    assert loaded.app.mtproxy.enabled is True
    assert loaded.app.mtproxy.port == 9443
    assert loaded.app.vless.daily_top_n == 15


def test_load_config_validation_errors(tmp_path: Path) -> None:
    env_path = tmp_path / "bad.env"
    env_path.write_text(
        "\n".join(
            [
                "WARN_PERCENT=100",
                "CRIT_PERCENT=90",
                "MTPROXY_ENABLE=1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_config(env_path)
    assert loaded.validation.ok is False
    assert any("WARN_PERCENT must be lower than CRIT_PERCENT" in e for e in loaded.validation.errors)
    assert any(
        "MTPROXY_ENABLE=1 requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        in e
        for e in loaded.validation.errors
    )
