"""Shared helpers for telegram_bot (timeouts, etc.)."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


def run_with_timeout(fn: Callable[[], T], timeout_sec: float) -> T:
    """Run blocking work in a worker thread; same wall-clock semantics as subprocess timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        return fut.result(timeout=timeout_sec)
