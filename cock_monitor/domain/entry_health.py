"""Entry-node health evaluation (VLESS accepts + TLS errors)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryAlertThresholds:
    accept_primary_min_per_min: float
    accept_secondary_min_per_min: float
    accept_ratio_warn: float
    accept_ratio_crit: float
    tls_handshake_warn: int
    tls_handshake_crit: int
    io_timeout_warn: int
    io_timeout_crit: int
    require_hop_ok: bool


@dataclass(frozen=True)
class EntryAlert:
    alert_type: str
    alert_key: str
    message: str
    level: str


def _rate_per_min(count: int, interval_sec: int) -> float:
    if interval_sec <= 0:
        return 0.0
    return count * 60.0 / interval_sec


def evaluate_entry_alerts(
    *,
    host: str,
    interval_sec: int,
    accepts_by_inbound: dict[str, int],
    primary_inbound: str,
    secondary_inbound: str,
    tls_handshake_delta: int,
    io_timeout_delta: int,
    hop_ok: bool,
    thresholds: EntryAlertThresholds,
) -> list[EntryAlert]:
    """Return alerts for TSPU-like entry degradation signals."""
    if interval_sec <= 0:
        return []

    alerts: list[EntryAlert] = []
    primary_count = int(accepts_by_inbound.get(primary_inbound, 0) or 0)
    secondary_count = int(accepts_by_inbound.get(secondary_inbound, 0) or 0)
    primary_rate = _rate_per_min(primary_count, interval_sec)
    secondary_rate = _rate_per_min(secondary_count, interval_sec)

    ratio: float | None = None
    if secondary_rate > 0:
        ratio = primary_rate / secondary_rate

    hop_gate = (not thresholds.require_hop_ok) or hop_ok

    if hop_gate and thresholds.accept_secondary_min_per_min > 0:
        secondary_alive = secondary_rate >= thresholds.accept_secondary_min_per_min
        primary_low = primary_rate < thresholds.accept_primary_min_per_min
        if secondary_alive and primary_low and ratio is not None:
            if thresholds.accept_ratio_crit > 0 and ratio <= thresholds.accept_ratio_crit:
                alerts.append(
                    EntryAlert(
                        alert_type="accept_asymmetry",
                        alert_key=f"accept_asymmetry:{primary_inbound}",
                        message=(
                            f"entry accept CRIT on {host}: {primary_inbound}={primary_rate:.1f}/min "
                            f"vs {secondary_inbound}={secondary_rate:.1f}/min "
                            f"(ratio={ratio:.2f} <= {thresholds.accept_ratio_crit})"
                        ),
                        level="CRIT",
                    )
                )
            elif thresholds.accept_ratio_warn > 0 and ratio <= thresholds.accept_ratio_warn:
                alerts.append(
                    EntryAlert(
                        alert_type="accept_asymmetry",
                        alert_key=f"accept_asymmetry:{primary_inbound}",
                        message=(
                            f"entry accept WARN on {host}: {primary_inbound}={primary_rate:.1f}/min "
                            f"vs {secondary_inbound}={secondary_rate:.1f}/min "
                            f"(ratio={ratio:.2f} <= {thresholds.accept_ratio_warn})"
                        ),
                        level="WARN",
                    )
                )

    if thresholds.tls_handshake_crit > 0 and tls_handshake_delta >= thresholds.tls_handshake_crit:
        alerts.append(
            EntryAlert(
                alert_type="tls_handshake_errors",
                alert_key="tls_handshake_errors",
                message=(
                    f"entry TLS handshake CRIT on {host}: "
                    f"+{tls_handshake_delta} in {interval_sec}s"
                ),
                level="CRIT",
            )
        )
    elif thresholds.tls_handshake_warn > 0 and tls_handshake_delta >= thresholds.tls_handshake_warn:
        alerts.append(
            EntryAlert(
                alert_type="tls_handshake_errors",
                alert_key="tls_handshake_errors",
                message=(
                    f"entry TLS handshake WARN on {host}: "
                    f"+{tls_handshake_delta} in {interval_sec}s"
                ),
                level="WARN",
            )
        )

    if thresholds.io_timeout_crit > 0 and io_timeout_delta >= thresholds.io_timeout_crit:
        alerts.append(
            EntryAlert(
                alert_type="io_timeout_errors",
                alert_key="io_timeout_errors",
                message=(
                    f"entry i/o timeout CRIT on {host}: +{io_timeout_delta} in {interval_sec}s"
                ),
                level="CRIT",
            )
        )
    elif thresholds.io_timeout_warn > 0 and io_timeout_delta >= thresholds.io_timeout_warn:
        alerts.append(
            EntryAlert(
                alert_type="io_timeout_errors",
                alert_key="io_timeout_errors",
                message=(
                    f"entry i/o timeout WARN on {host}: +{io_timeout_delta} in {interval_sec}s"
                ),
                level="WARN",
            )
        )

    return alerts
