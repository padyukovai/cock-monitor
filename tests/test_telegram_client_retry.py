from __future__ import annotations

import io
import json
import urllib.parse
import urllib.error
import urllib.request

import pytest
from telegram_bot.telegram_client import TelegramClient


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def test_send_message_retries_transient_http_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TelegramClient("token")
    calls = {"n": 0}

    def _fake_urlopen(_req: object, timeout: int = 0) -> _FakeResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                url="https://example",
                code=502,
                msg="bad gateway",
                hdrs=None,
                fp=io.BytesIO(b'{"ok":false}'),
            )
        return _FakeResponse({"ok": True, "result": {}})

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr("telegram_bot.telegram_client.time.sleep", lambda _x: None)
    monkeypatch.setattr("telegram_bot.telegram_client.random.uniform", lambda _a, _b: 0.0)

    result = client.send_message_with_result("chat", "hello")

    assert result.success is True
    assert result.attempts == 2
    assert calls["n"] == 2


def test_send_message_does_not_retry_non_transient_http(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TelegramClient("token")
    calls = {"n": 0}

    def _fake_urlopen(_req: object, timeout: int = 0) -> _FakeResponse:
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="https://example",
            code=400,
            msg="bad request",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr("telegram_bot.telegram_client.time.sleep", lambda _x: None)
    monkeypatch.setattr("telegram_bot.telegram_client.random.uniform", lambda _a, _b: 0.0)

    result = client.send_message_with_result("chat", "hello")

    assert result.success is False
    assert "HTTP 400" in result.reason
    assert calls["n"] == 1


def test_send_message_retry_exhausted_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TelegramClient("token")
    calls = {"n": 0}

    def _fake_urlopen(_req: object, timeout: int = 0) -> _FakeResponse:
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="https://example",
            code=503,
            msg="service unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr("telegram_bot.telegram_client.time.sleep", lambda _x: None)
    monkeypatch.setattr("telegram_bot.telegram_client.random.uniform", lambda _a, _b: 0.0)

    result = client.send_message_with_result("chat", "hello")

    assert result.success is False
    assert "HTTP 503" in result.reason
    assert result.attempts == 3
    assert calls["n"] == 3


def test_set_my_commands_posts_commands_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TelegramClient("token")
    captured: dict[str, object] = {}

    def _fake_urlopen(req: object, timeout: int = 0) -> _FakeResponse:
        captured["url"] = getattr(req, "full_url", "")
        captured["data"] = getattr(req, "data", b"")
        captured["timeout"] = timeout
        return _FakeResponse({"ok": True, "result": True})

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    client.set_my_commands([("status", "Full conntrack status"), ("help", "Show command help")])

    assert str(captured["url"]).endswith("/setMyCommands")
    body = str(captured["data"], "utf-8")
    params = urllib.parse.parse_qs(body)
    commands_json = params["commands"][0]
    commands = json.loads(commands_json)
    assert commands[0]["command"] == "status"
    assert commands[1]["description"] == "Show command help"
