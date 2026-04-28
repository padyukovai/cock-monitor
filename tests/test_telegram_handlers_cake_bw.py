from __future__ import annotations

from pathlib import Path

from telegram_bot.handlers import handle_update


class _Client:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, _chat_id: str, text: str, parse_mode: str | None = None) -> None:  # noqa: ARG002
        self.messages.append(text)


def _update(text: str) -> dict[str, object]:
    return {"message": {"chat": {"id": "1"}, "text": text}}


def test_cake_bw_updates_max_rate_in_env(tmp_path: Path) -> None:
    env_file = tmp_path / "cock.env"
    env_file.write_text("SHAPER_MIN_RATE_MBIT=10\nSHAPER_MAX_RATE_MBIT=100\n", encoding="utf-8")
    client = _Client()

    handle_update(
        _update("/cake_bw 250"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    new_env = env_file.read_text(encoding="utf-8")
    assert "SHAPER_MAX_RATE_MBIT=250" in new_env
    assert len(client.messages) == 1
    assert "Updated SHAPER_MAX_RATE_MBIT=250M" in client.messages[0]


def test_cake_bw_rejects_below_min_rate(tmp_path: Path) -> None:
    env_file = tmp_path / "cock.env"
    env_file.write_text("SHAPER_MIN_RATE_MBIT=20\nSHAPER_MAX_RATE_MBIT=100\n", encoding="utf-8")
    client = _Client()

    handle_update(
        _update("/cake_bw 10"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    current_env = env_file.read_text(encoding="utf-8")
    assert "SHAPER_MAX_RATE_MBIT=100" in current_env
    assert len(client.messages) == 1
    assert "Rejected" in client.messages[0]


def test_cake_bw_requires_single_integer_argument(tmp_path: Path) -> None:
    env_file = tmp_path / "cock.env"
    env_file.write_text("SHAPER_MAX_RATE_MBIT=100\n", encoding="utf-8")
    client = _Client()

    handle_update(
        _update("/cake_bw"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )
    assert client.messages == ["Usage: /cake_bw <mbit> [--force]"]

    client.messages.clear()
    handle_update(
        _update("/cake_bw x"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )
    assert client.messages == ["Invalid value. Must be integer Mbit."]


def test_cake_bw_rejects_unknown_flag(tmp_path: Path) -> None:
    env_file = tmp_path / "cock.env"
    env_file.write_text("SHAPER_MAX_RATE_MBIT=100\n", encoding="utf-8")
    client = _Client()

    handle_update(
        _update("/cake_bw 120 --bad"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    assert client.messages == ["Unknown flag. Usage: /cake_bw <mbit> [--force]"]


def test_cake_bw_force_applies_global_tc_limit(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "cock.env"
    env_file.write_text("SHAPER_IFACE=eth0\nSHAPER_MIN_RATE_MBIT=10\nSHAPER_MAX_RATE_MBIT=100\n", encoding="utf-8")
    client = _Client()
    seen: list[tuple[str, int]] = []

    monkeypatch.setattr("telegram_bot.handlers.run_with_timeout", lambda fn, _timeout: fn())
    def _fake_apply_global_cake_limit(*, iface: str, rate_mbit: int) -> str:
        seen.append((iface, rate_mbit))
        return f"Applied global CAKE limit on {iface}: {rate_mbit}M"

    monkeypatch.setattr(
        "telegram_bot.handlers._apply_global_cake_limit",
        _fake_apply_global_cake_limit,
    )

    handle_update(
        _update("/cake_bw 222 --force"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    assert seen == [("eth0", 222)]
    assert "SHAPER_MAX_RATE_MBIT=222" in env_file.read_text(encoding="utf-8")
    assert len(client.messages) == 1
    assert "Applied global CAKE limit on eth0: 222M" in client.messages[0]
