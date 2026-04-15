"""Default paths and values aligned with config.example.env and README."""

from __future__ import annotations

from pathlib import Path

DEFAULT_METRICS_DB = "/var/lib/cock-monitor/metrics.db"
DEFAULT_COCK_MONITOR_HOME = "/opt/cock-monitor"
DEFAULT_STATE_FILE = "/var/lib/cock-monitor/state"
DEFAULT_ENV_FILE = Path("/etc/cock-monitor.env")
