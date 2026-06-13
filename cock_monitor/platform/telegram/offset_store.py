from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_offset(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    if not text:
        return 0
    try:
        return max(0, int(text))
    except ValueError:
        return 0


def write_offset(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".telegram_offset.",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(int(value)))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
