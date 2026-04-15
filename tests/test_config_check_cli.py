from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_config_check_cli_ok(tmp_path: Path) -> None:
    env_file = tmp_path / "ok.env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=t",
                "TELEGRAM_CHAT_ID=1",
                "WARN_PERCENT=80",
                "CRIT_PERCENT=95",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "cock_monitor", "config-check", str(env_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert proc.returncode == 0
    assert "ok: config is valid" in proc.stdout


def test_config_check_cli_fails_on_invalid_range(tmp_path: Path) -> None:
    env_file = tmp_path / "bad.env"
    env_file.write_text("WARN_PERCENT=100\nCRIT_PERCENT=90\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "cock_monitor", "config-check", str(env_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert proc.returncode == 1
    assert "ERROR: WARN_PERCENT must be lower than CRIT_PERCENT" in proc.stdout
