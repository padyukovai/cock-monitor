from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_conntrack_check_cli_dry_run_minimal_env(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    state_file = tmp_path / "state"
    env_file.write_text(
        "\n".join(
            [
                "DRY_RUN=1",
                "CHECK_CONNTRACK_FILL=0",
                "ALERT_ON_STATS=0",
                "ALERT_ON_STATS_DELTA=0",
                "METRICS_RECORD_EVERY_RUN=0",
                f"STATE_FILE={state_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "cock_monitor", "conntrack-check", str(env_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
