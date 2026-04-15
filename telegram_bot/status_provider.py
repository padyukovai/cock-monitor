from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Protocol

from cock_monitor.services.status_report import StatusReportError, build_status_report

from telegram_bot.runtime import run_with_timeout


class StatusProvider(Protocol):
    def get_status(self) -> tuple[bool, str]:
        """Return (success, text). On failure, text may be stderr or a short message."""


MAX_MESSAGE_LEN = 4096


def truncate_for_telegram(text: str, limit: int = MAX_MESSAGE_LEN) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n…(truncated)"
    return text[: max(0, limit - len(suffix))] + suffix


class PythonStatusProvider:
    """Builds /status text via Python service without shell orchestration."""

    def __init__(
        self,
        *,
        env_file: Path,
        timeout_sec: float = 60.0,
    ) -> None:
        self._env_file = env_file.resolve()
        self._timeout = timeout_sec

    def get_status(self) -> tuple[bool, str]:
        try:
            out = run_with_timeout(
                lambda: build_status_report(self._env_file),
                self._timeout,
            )
        except FutureTimeout:
            return False, f"status timed out after {self._timeout:.0f}s"
        except StatusReportError as exc:
            return False, str(exc)
        except OSError as exc:
            return False, str(exc)
        return True, out if out else "(empty status)"
