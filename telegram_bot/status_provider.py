from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class StatusProvider(Protocol):
    def get_status(self) -> tuple[bool, str]:
        """Return (success, text). On failure, text may be stderr or a short message."""


MAX_MESSAGE_LEN = 4096


def truncate_for_telegram(text: str, limit: int = MAX_MESSAGE_LEN) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n…(truncated)"
    return text[: max(0, limit - len(suffix))] + suffix


class SubprocessStatusProvider:
    """Runs cock-status.sh with the same env file as the monitor."""

    def __init__(
        self,
        *,
        env_file: Path,
        cock_status_sh: Path,
        timeout_sec: float = 60.0,
    ) -> None:
        self._env_file = env_file.resolve()
        self._script = cock_status_sh.resolve()
        self._timeout = timeout_sec

    def get_status(self) -> tuple[bool, str]:
        if not self._script.is_file():
            return False, f"cock-status script not found: {self._script}"
        try:
            proc = subprocess.run(
                ["/usr/bin/env", "bash", str(self._script), str(self._env_file)],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "cock-status timed out"
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            msg = err or out or f"exit {proc.returncode}"
            return False, msg
        return True, out if out else "(empty status)"
