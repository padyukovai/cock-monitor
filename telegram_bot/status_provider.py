"""Compatibility shim for status provider."""

from __future__ import annotations

from pathlib import Path

from cock_monitor.modules.core.status import build_core_status
from cock_monitor.platform.telegram.runtime import run_with_timeout


class StatusProvider:
    def get_status(self) -> tuple[bool, str]:
        raise NotImplementedError


class PythonStatusProvider(StatusProvider):
    def __init__(self, *, env_file: Path, timeout_sec: float = 120.0) -> None:
        self._env_file = env_file
        self._timeout_sec = timeout_sec

    def get_status(self) -> tuple[bool, str]:
        def _fn() -> str:
            return build_core_status(self._env_file)

        try:
            return True, run_with_timeout(_fn, self._timeout_sec)
        except OSError as e:
            return False, str(e)
        except RuntimeError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)


def truncate_for_telegram(text: str, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


# Legacy alias for tests that monkeypatch build_status_report
build_status_report = build_core_status

__all__ = [
    "StatusProvider",
    "PythonStatusProvider",
    "truncate_for_telegram",
    "run_with_timeout",
    "build_status_report",
]
