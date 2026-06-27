from __future__ import annotations

from pathlib import Path

from cock_monitor.modules.mtproxy.config import MtproxyConfig
from cock_monitor.platform.telegram.config import BotConfig
from cock_monitor.platform.telegram.poll_once import poll_once


def _cfg(tmp_path: Path, *, max_updates: int, max_seconds: int) -> BotConfig:
    env_file = tmp_path / "env"
    env_file.write_text(
        "ENABLED_MODULES=core\nTELEGRAM_BOT_TOKEN=token\nTELEGRAM_CHAT_ID=1\n",
        encoding="utf-8",
    )
    return BotConfig(
        env_file=env_file,
        env={"ENABLED_MODULES": "core"},
        bot_token="token",
        chat_id="1",
        offset_file=tmp_path / "offset",
        monitor_home=tmp_path,
        mtproxy=MtproxyConfig.from_env_map({}),
        shaper_enabled=False,
        max_updates_per_run=max_updates,
        max_seconds_per_run=max_seconds,
        proxy_url=None,
    )


_POLL_ONCE = "cock_monitor.platform.telegram.poll_once"


def _patch_offset_tracking(
    monkeypatch,
    seen: list[int],
    written_offsets: list[int],
    *,
    offset: int = 1,
) -> None:
    def _write_offset(_p, off: int) -> None:
        written_offsets.append(off)

    def _handle_update(u, **_k) -> None:
        seen.append(int(u["update_id"]))

    monkeypatch.setattr(f"{_POLL_ONCE}.read_offset", lambda _p: offset)
    monkeypatch.setattr(f"{_POLL_ONCE}.write_offset", _write_offset)
    monkeypatch.setattr(f"{_POLL_ONCE}.handle_update", _handle_update)


def test_poll_once_stops_by_max_updates(
    tmp_path: Path, monkeypatch
) -> None:
    updates = [{"update_id": i} for i in range(1, 11)]
    seen: list[int] = []
    written_offsets: list[int] = []

    class _Client:
        def __init__(self, _token: str, proxy_url: str | None = None) -> None:
            pass

        def set_my_commands(self, _commands: list[tuple[str, str]]) -> None:
            pass

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            if updates:
                batch, updates[:] = updates[:5], updates[5:]
                return batch
            return []

    monkeypatch.setattr(f"{_POLL_ONCE}.TelegramClient", _Client)
    _patch_offset_tracking(monkeypatch, seen, written_offsets)

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
        def __init__(self, _token: str, proxy_url: str | None = None) -> None:
            pass

        def set_my_commands(self, _commands: list[tuple[str, str]]) -> None:
            pass

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            return list(updates)

    monkeypatch.setattr(f"{_POLL_ONCE}.TelegramClient", _Client)
    _patch_offset_tracking(monkeypatch, seen, written_offsets)
    monkeypatch.setattr(f"{_POLL_ONCE}.time.monotonic", lambda: next(times))

    poll_once(_cfg(tmp_path, max_updates=100, max_seconds=2))

    assert seen == [1, 2]
    assert written_offsets[-1] == 3


def test_poll_once_sets_menu_commands_with_mtproxy_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[list[tuple[str, str]]] = []

    class _Client:
        def __init__(self, _token: str, proxy_url: str | None = None) -> None:
            pass

        def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
            captured.append(commands)

        def get_updates(self, _offset: int, timeout: int = 0):  # noqa: ARG002
            return []

    monkeypatch.setattr(f"{_POLL_ONCE}.TelegramClient", _Client)
    monkeypatch.setattr(f"{_POLL_ONCE}.read_offset", lambda _p: 1)

    poll_once(_cfg(tmp_path, max_updates=3, max_seconds=999))

    assert captured
    assert ("status", "Full host + conntrack status") in captured[0]
    assert ("help", "Show enabled module commands") in captured[0]
    assert not any(name.startswith("mt_") for name, _ in captured[0])
    assert not any(name == "cake_bw" for name, _ in captured[0])
