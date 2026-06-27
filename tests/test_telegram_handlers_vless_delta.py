from __future__ import annotations

from pathlib import Path

from cock_monitor.platform.telegram.dispatch import handle_update


class _Client:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, _chat_id: str, text: str, parse_mode: str | None = None) -> None:  # noqa: ARG002
        self.messages.append(text)


def _update(text: str) -> dict[str, object]:
    return {"message": {"chat": {"id": "1"}, "text": text}}


def _write_env(path: Path, extra: str = "") -> None:
    path.write_text(f"ENABLED_MODULES=core,vless\n{extra}", encoding="utf-8")


def test_vless_delta_defaults_to_daily_mode(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    env_file = tmp_path / "env"
    _write_env(env_file)
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_with_timeout",
        lambda fn, _timeout: fn(),
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_daily_with_telegram",
        lambda _env_file: calls.append("daily"),
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_since_last_sent_with_telegram",
        lambda _env_file: calls.append("since-last"),
    )

    client = _Client()
    handle_update(
        _update("/vless_delta"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    assert calls == ["daily"]
    assert not client.messages


def test_vless_delta_since_last_mode_by_flag(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    env_file = tmp_path / "env"
    _write_env(env_file)
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_with_timeout",
        lambda fn, _timeout: fn(),
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_daily_with_telegram",
        lambda _env_file: calls.append("daily"),
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_since_last_sent_with_telegram",
        lambda _env_file: calls.append("since-last"),
    )

    client = _Client()
    handle_update(
        _update("/vless_delta --since-last-sent"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    assert calls == ["since-last"]
    assert not client.messages


def test_vless_delta_unknown_flag_returns_usage(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "env"
    _write_env(env_file)
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_with_timeout",
        lambda fn, _timeout: fn(),
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_daily_with_telegram",
        lambda _env_file: None,
    )
    monkeypatch.setattr(
        "cock_monitor.platform.telegram.dispatch.run_since_last_sent_with_telegram",
        lambda _env_file: None,
    )

    client = _Client()
    handle_update(
        _update("/vless_delta --unknown"),
        allowed_chat_id="1",
        client=client,
        status_provider=object(),  # type: ignore[arg-type]
        env_file=env_file,
        mtproxy_cfg=None,
    )

    assert len(client.messages) == 1
    assert "Unknown flag for /vless_delta" in client.messages[0]
