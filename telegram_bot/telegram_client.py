from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


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
            with urllib.request.urlopen(req, timeout=max(30, timeout + 5)) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"getUpdates HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"getUpdates network error: {e}") from e
        if not body.get("ok"):
            raise RuntimeError(f"getUpdates API error: {body!r}")
        return list(body.get("result") or [])

    def send_message(self, chat_id: str, text: str) -> None:
        url = self._base + "sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"sendMessage HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"sendMessage network error: {e}") from e
        if not body.get("ok"):
            raise RuntimeError(f"sendMessage API error: {body!r}")
