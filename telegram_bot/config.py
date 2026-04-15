from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cock_monitor.defaults import DEFAULT_COCK_MONITOR_HOME
from cock_monitor.env import parse_env_file
from mtproxy_module.config import MtproxyConfig


def default_offset_path(env: Mapping[str, str]) -> str:
    state_file = env.get("STATE_FILE", "/var/lib/cock-monitor/state")
    parent = str(Path(state_file).expanduser().resolve().parent)
    return str(Path(parent) / "telegram_offset")


@dataclass(frozen=True)
class BotConfig:
    env_file: Path
    env: Mapping[str, str]
    bot_token: str
    chat_id: str
    offset_file: Path
    monitor_home: Path
    mtproxy: MtproxyConfig

    @classmethod
    def from_env_file(cls, env_path: Path) -> BotConfig:
        env_path = env_path.expanduser().resolve()
        raw = parse_env_file(env_path)
        token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the env file"
            )
        offset = raw.get("TELEGRAM_OFFSET_FILE", "").strip() or default_offset_path(raw)
        home = os.environ.get("COCK_MONITOR_HOME", DEFAULT_COCK_MONITOR_HOME)
        return cls(
            env_file=env_path,
            env=raw,
            bot_token=token,
            chat_id=chat_id,
            offset_file=Path(offset).expanduser(),
            monitor_home=Path(home).expanduser().resolve(),
            mtproxy=MtproxyConfig.from_env_map(raw),
        )
