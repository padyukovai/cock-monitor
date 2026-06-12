from __future__ import annotations

from telegram_bot.handlers import bot_commands


def test_bot_commands_hides_mtproxy_and_shaper_when_disabled() -> None:
    commands = bot_commands(mtproxy_enabled=False, shaper_enabled=False)
    names = [name for name, _ in commands]
    assert names == ["status", "chart", "vless_delta", "help"]
    assert not any(name.startswith("mt_") for name in names)
    assert "cake_bw" not in names


def test_bot_commands_includes_optional_modules_when_enabled() -> None:
    commands = bot_commands(mtproxy_enabled=True, shaper_enabled=True)
    names = [name for name, _ in commands]
    assert "cake_bw" in names
    assert "mt_status" in names
    assert "mt_today" in names
    assert "mt_threshold" in names
