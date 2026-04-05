from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def default_offset_path(env: Mapping[str, str]) -> str:
    state_file = env.get("STATE_FILE", "/var/lib/cock-monitor/state")
    parent = str(Path(state_file).expanduser().resolve().parent)
    return str(Path(parent) / "telegram_offset")


@dataclass(frozen=True)
class BotConfig:
    env_file: Path
    bot_token: str
    chat_id: str
    offset_file: Path
    monitor_home: Path

    @classmethod
    def from_env_file(cls, env_path: Path) -> BotConfig:
        env_path = env_path.expanduser().resolve()
        raw = _parse_env_file(env_path)
        token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the env file"
            )
        offset = raw.get("TELEGRAM_OFFSET_FILE", "").strip() or default_offset_path(raw)
        home = os.environ.get("COCK_MONITOR_HOME", "/opt/cock-monitor")
        return cls(
            env_file=env_path,
            bot_token=token,
            chat_id=chat_id,
            offset_file=Path(offset).expanduser(),
            monitor_home=Path(home).expanduser().resolve(),
        )
