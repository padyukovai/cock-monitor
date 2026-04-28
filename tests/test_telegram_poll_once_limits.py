from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from telegram_bot.poll_once import poll_once


def _cfg(tmp_path: Path, *, max_updates: int, max_seconds: int) -> SimpleNamespace:
    return SimpleNamespace(
        bot_token="token",
        offset_file=tmp_path / "offset",
        env_file=tmp_path / "env",
        chat_id="1",
        mtproxy=SimpleNamespace(enabled=False),
        max_updates_per_run=max_updates,
        max_seconds_per_run=max_seconds,
    )


def test_poll_once_stops_by_max_updates(
    tmp_path: Path, monkeypatch
) -> None:
    updates = [{"update_id": i} for i in range(1, 11)]
    seen: list[int] = []
    written_offsets: list[int] = []

    class _Client:
        def __init__(self, _token: str) -> None:
            pass

        def set_my_commands(self, _commands: list[tuple[str, str]]) -> None:
            pass

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            if updates:
                batch, updates[:] = updates[:5], updates[5:]
                return batch
            return []

    monkeypatch.setattr("telegram_bot.poll_once.TelegramClient", _Client)
    monkeypatch.setattr("telegram_bot.poll_once.read_offset", lambda _p: 1)
    monkeypatch.setattr("telegram_bot.poll_once.write_offset", lambda _p, off: written_offsets.append(off))
    monkeypatch.setattr("telegram_bot.poll_once.PythonStatusProvider", lambda **_k: object())
    monkeypatch.setattr("telegram_bot.poll_once.handle_update", lambda u, **_k: seen.append(int(u["update_id"])))

    poll_once(_cfg(tmp_path, max_updates=3, max_seconds=999))

    assert seen == [1, 2, 3]
    assert written_offsets[-1] == 4


def test_poll_once_stops_by_max_seconds(
    tmp_path: Path, monkeypatch
) -> None:
    updates = [{"update_id": i} for i in range(1, 6)]
    seen: list[int] = []
    written_offsets: list[int] = []
    times = iter([0.0, 0.0, 1.0, 2.1, 2.1, 2.1])

    class _Client:
        def __init__(self, _token: str) -> None:
            pass

        def set_my_commands(self, _commands: list[tuple[str, str]]) -> None:
            pass

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            return list(updates)

    monkeypatch.setattr("telegram_bot.poll_once.TelegramClient", _Client)
    monkeypatch.setattr("telegram_bot.poll_once.read_offset", lambda _p: 1)
    monkeypatch.setattr("telegram_bot.poll_once.write_offset", lambda _p, off: written_offsets.append(off))
    monkeypatch.setattr("telegram_bot.poll_once.PythonStatusProvider", lambda **_k: object())
    monkeypatch.setattr("telegram_bot.poll_once.handle_update", lambda u, **_k: seen.append(int(u["update_id"])))
    monkeypatch.setattr("telegram_bot.poll_once.time.monotonic", lambda: next(times))

    poll_once(_cfg(tmp_path, max_updates=100, max_seconds=2))

    assert seen == [1, 2]
    assert written_offsets[-1] == 3


def test_poll_once_sets_menu_commands_with_mtproxy_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[list[tuple[str, str]]] = []

    class _Client:
        def __init__(self, _token: str) -> None:
            pass

        def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
            captured.append(commands)

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            return []

    monkeypatch.setattr("telegram_bot.poll_once.TelegramClient", _Client)
    monkeypatch.setattr("telegram_bot.poll_once.read_offset", lambda _p: 1)
    monkeypatch.setattr("telegram_bot.poll_once.PythonStatusProvider", lambda **_k: object())

    poll_once(_cfg(tmp_path, max_updates=3, max_seconds=999))

    assert captured
    assert ("status", "Full conntrack status") in captured[0]
    assert ("help", "Show command help") in captured[0]
    assert not any(name.startswith("mt_") for name, _ in captured[0])
