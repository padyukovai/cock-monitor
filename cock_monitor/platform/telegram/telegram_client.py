from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    reason: str
    attempts: int


class TelegramRequestError(RuntimeError):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient
        self.attempts = 1


def _is_transient_http_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _retry_with_backoff(
    action: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.5,
    jitter_sec: float = 0.2,
) -> tuple[T, int]:
    if attempts < 1:
        attempts = 1
    last_exc: Exception | None = None
    for idx in range(attempts):
        try:
            return action(), idx + 1
        except TelegramRequestError as e:
            last_exc = e
            e.attempts = idx + 1
            if not e.transient or idx == attempts - 1:
                raise
            delay = base_delay_sec * (2**idx) + random.uniform(0, jitter_sec)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class TelegramClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._base = f"https://api.telegram.org/bot{token}/"

    def get_updates(
        self,
        offset: int,
        *,
        timeout: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "timeout": str(timeout),
            "allowed_updates": json.dumps(["message"]),
        }
        if offset > 0:
            params["offset"] = str(offset)
        url = self._base + "getUpdates?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET")
        try:
            body, _ = _retry_with_backoff(
                lambda: self._request_json(req, timeout=max(30, timeout + 5), operation="getUpdates")
            )
        except TelegramRequestError as e:
            raise RuntimeError(str(e)) from e
        if not body.get("ok"):
            raise RuntimeError(f"getUpdates API error: {body!r}")
        return list(body.get("result") or [])

    def send_message(self, chat_id: str, text: str, *, parse_mode: str = "") -> None:
        result = self.send_message_with_result(chat_id, text, parse_mode=parse_mode)
        if not result.success:
            raise RuntimeError(result.reason)

    def send_message_with_result(self, chat_id: str, text: str, *, parse_mode: str = "") -> DeliveryResult:
        url = self._base + "sendMessage"
        payload: dict[str, str] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            body, used_attempts = _retry_with_backoff(
                lambda: self._request_json(req, timeout=60, operation="sendMessage")
            )
        except TelegramRequestError as e:
            return DeliveryResult(success=False, reason=str(e), attempts=e.attempts)
        if not body.get("ok"):
            reason = f"sendMessage API error: {body!r}"
            return DeliveryResult(success=False, reason=reason, attempts=used_attempts)
        return DeliveryResult(success=True, reason="", attempts=used_attempts)

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        url = self._base + "setMyCommands"
        payload = {
            "commands": [{"command": command, "description": description} for command, description in commands]
        }
        data = urllib.parse.urlencode({"commands": json.dumps(payload["commands"])}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            body, _ = _retry_with_backoff(
                lambda: self._request_json(req, timeout=60, operation="setMyCommands")
            )
        except TelegramRequestError as e:
            raise RuntimeError(str(e)) from e
        if not body.get("ok"):
            raise RuntimeError(f"setMyCommands API error: {body!r}")

    def send_photo(
        self,
        chat_id: str,
        photo_path: str | Path,
        *,
        caption: str = "",
    ) -> None:
        photo_path = Path(photo_path)
        boundary = f"----------{uuid.uuid4().hex}"
        bin_data = photo_path.read_bytes()
        fname = photo_path.name or "chart.png"
        crlf = b"\r\n"
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            + chat_id.encode("utf-8")
            + crlf
        )
        if caption:
            cap = caption if len(caption) <= 1024 else caption[:1021] + "..."
            body.extend(f'--{boundary}\r\n'.encode())
            body.extend(
                b'Content-Disposition: form-data; name="caption"\r\n\r\n'
                + cap.encode("utf-8")
                + crlf
            )
        body.extend(f"--{boundary}\r\n".encode())
        disp = (
            f'Content-Disposition: form-data; name="photo"; filename="{fname}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        )
        body.extend(disp.encode("utf-8"))
        body.extend(bin_data)
        body.extend(crlf)
        body.extend(f"--{boundary}--\r\n".encode())

        url = self._base + "sendPhoto"
        req = urllib.request.Request(
            url,
            data=bytes(body),
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            out, _ = _retry_with_backoff(lambda: self._request_json(req, timeout=120, operation="sendPhoto"))
        except TelegramRequestError as e:
            raise RuntimeError(str(e)) from e
        if not out.get("ok"):
            raise RuntimeError(f"sendPhoto API error: {out!r}")

    def _request_json(
        self,
        req: urllib.request.Request,
        *,
        timeout: int,
        operation: str,
    ) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise TelegramRequestError(
                f"{operation} HTTP {e.code}: {err_body}",
                transient=_is_transient_http_status(e.code),
            ) from e
        except urllib.error.URLError as e:
            raise TelegramRequestError(
                f"{operation} network error: {e}",
                transient=True,
            ) from e
