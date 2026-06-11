"""CLI smoke tests for burst-capture."""
from __future__ import annotations

from pathlib import Path

from cock_monitor import burst_capture_cli


def test_burst_capture_report_missing_file(tmp_path: Path) -> None:
    rc = burst_capture_cli.run(["report", str(tmp_path / "missing.jsonl")])
    assert rc == 1


def test_burst_capture_requires_env_file() -> None:
    rc = burst_capture_cli.run(["start", "--duration", "10"])
    assert rc == 2
