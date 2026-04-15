from __future__ import annotations

import io
import json
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
