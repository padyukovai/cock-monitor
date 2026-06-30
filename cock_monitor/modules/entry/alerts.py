"""Entry module alert evaluation (re-export domain logic)."""

from __future__ import annotations

from cock_monitor.domain.entry_health import EntryAlert, EntryAlertThresholds, evaluate_entry_alerts

__all__ = [
    "EntryAlert",
    "EntryAlertThresholds",
    "evaluate_entry_alerts",
]
