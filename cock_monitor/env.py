"""Parse `.env`-style files and optionally merge into the process environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def parse_env_file(path: Path) -> dict[str, str]:
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


def merge_env_into_process(raw: Mapping[str, str]) -> None:
    for k, v in raw.items():
        if k not in os.environ:
            os.environ[k] = v
