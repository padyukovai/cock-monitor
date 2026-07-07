"""Incident module env loading and defaults."""

from __future__ import annotations

import os
from pathlib import Path

from cock_monitor.platform.env_runtime import get_int, get_str, load_env_overwrite

__all__ = [
    "apply_incident_defaults",
    "get_int",
    "get_str",
    "load_env_overwrite",
    "resolve_env_file",
]


def apply_incident_defaults() -> None:
    """Default env keys for incident sampler ticks."""
    os.environ.setdefault("INCIDENT_LOG_DIR", "/var/lib/cock-monitor")
    os.environ.setdefault("INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state")

    os.environ.setdefault("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8")
    os.environ.setdefault("INCIDENT_PING_INTERNAL_TARGETS", "")
    os.environ.setdefault(
        "INCIDENT_PING_EXTERNAL_TARGETS",
        os.environ.get("INCIDENT_PING_TARGETS", "1.1.1.1 8.8.8.8"),
    )
    os.environ["INCIDENT_PING_TARGETS"] = os.environ["INCIDENT_PING_EXTERNAL_TARGETS"]

    os.environ.setdefault("INCIDENT_PING_COUNT", "2")
    os.environ.setdefault("INCIDENT_PING_TIMEOUT_SEC", "1")
    os.environ.setdefault("INCIDENT_PING_LOSS_WARN_PCT", "20")
    os.environ.setdefault("INCIDENT_WARN_CONSECUTIVE", "1")
    os.environ.setdefault("INCIDENT_TCP_PROBE_LOCAL_TARGET", "127.0.0.1")
    os.environ.setdefault("INCIDENT_TCP_PROBE_EXTERNAL_TARGET", "")
    os.environ.setdefault("INCIDENT_TCP_PROBE_PORTS", "")
    os.environ.setdefault("INCIDENT_TCP_PROBE_TIMEOUT_SEC", "2")
    os.environ.setdefault("INCIDENT_TCP_PROBE_WARN_FAILS", "1")
    os.environ.setdefault("INCIDENT_TCP_PROBE_CRIT_FAILS", "0")

    os.environ.setdefault("INCIDENT_TCP_FIN_WAIT_WARN", "0")
    os.environ.setdefault("INCIDENT_TCP_CLOSE_WAIT_WARN", "0")
    os.environ.setdefault("INCIDENT_TCP_ORPHAN_WARN", "0")

    os.environ.setdefault("INCIDENT_HOP_ESTAB_WARN", "5")
    os.environ.setdefault("INCIDENT_HOP_ESTAB_CRIT", "20")
    os.environ.setdefault("INCIDENT_HOP_FIN_WAIT_WARN", "20")
    os.environ.setdefault("INCIDENT_HOP_FIN_WAIT_CRIT", "50")

    os.environ.setdefault("INCIDENT_DNS_HOST", "api.telegram.org")
    os.environ.setdefault("INCIDENT_DNS_TIMEOUT_SEC", "2")
    os.environ.setdefault("INCIDENT_DNS_FAIL_STREAK_WARN", "3")

    os.environ.setdefault("INCIDENT_CONNTRACK_WARN_PCT", "85")
    os.environ.setdefault("INCIDENT_CONNTRACK_CRIT_PCT", "95")

    os.environ.setdefault("INCIDENT_SYSTEMD_UNITS", "x-ui.service")

    os.environ.setdefault("INCIDENT_ALERT_ENABLE", "0")
    os.environ.setdefault("INCIDENT_ALERT_COOLDOWN_SEC", "300")
    os.environ.setdefault("INCIDENT_POSTMORTEM_ENABLE", "1")
    os.environ.setdefault("DRY_RUN", "0")

    os.environ.setdefault("INCIDENT_LEAK_INVESTIGATION", "0")
    os.environ.setdefault("INCIDENT_LEAK_INVESTIGATION_HOURS", "24")
    os.environ.setdefault("INCIDENT_LEAK_STATE_FILE", "/var/lib/cock-monitor/leak_investigation.state")
    os.environ.setdefault("INCIDENT_LEAK_AUTO_REPORT", "1")


def resolve_env_file(argv0: str | None) -> Path | None:
    if argv0:
        return Path(argv0).expanduser().resolve()
    ef = os.environ.get("ENV_FILE", "").strip()
    if ef:
        return Path(ef).expanduser().resolve()
    return None
