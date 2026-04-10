from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
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

    def send_message(self, chat_id: str, text: str, *, parse_mode: str = "") -> None:
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
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"sendMessage HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"sendMessage network error: {e}") from e
        if not body.get("ok"):
            raise RuntimeError(f"sendMessage API error: {body!r}")

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
            with urllib.request.urlopen(req, timeout=120) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"sendPhoto HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"sendPhoto network error: {e}") from e
        if not out.get("ok"):
            raise RuntimeError(f"sendPhoto API error: {out!r}")
