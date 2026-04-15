"""Read-only access to 3x-ui (X-UI) SQLite: client traffics and VLESS inbound emails."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficRow:
    email: str
    up: int
    down: int

    @property
    def total(self) -> int:
        return self.up + self.down


def safe_i64(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _extract_vless_emails(settings_text: str) -> set[str]:
    out: set[str] = set()
    try:
        payload = json.loads(settings_text or "{}")
    except json.JSONDecodeError:
        return out
    clients = payload.get("clients")
    if not isinstance(clients, list):
        return out
    for client in clients:
        if not isinstance(client, dict):
            continue
        email = str(client.get("email", "")).strip()
        if email:
            out.add(email)
    return out


def fetch_vless_email_set(conn: sqlite3.Connection) -> set[str]:
    emails: set[str] = set()
    cur = conn.execute(
        """
        SELECT protocol, settings
        FROM inbounds
        WHERE protocol IS NOT NULL
        """
    )
    for protocol, settings in cur.fetchall():
        if str(protocol).strip().lower() != "vless":
            continue
        if not isinstance(settings, str):
            continue
        emails.update(_extract_vless_emails(settings))
    return emails


def fetch_client_traffics(conn: sqlite3.Connection) -> list[TrafficRow]:
    cur = conn.execute(
        """
        SELECT email, COALESCE(up, 0) AS up_bytes, COALESCE(down, 0) AS down_bytes
        FROM client_traffics
        WHERE email IS NOT NULL
          AND TRIM(email) <> ''
        """
    )
    rows: list[TrafficRow] = []
    for email, up, down in cur.fetchall():
        rows.append(TrafficRow(email=str(email).strip(), up=safe_i64(up), down=safe_i64(down)))
    return rows
