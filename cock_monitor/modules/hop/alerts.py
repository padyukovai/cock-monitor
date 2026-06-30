"""Hop alert evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HopAlertThresholds:
    estab_warn: int
    estab_crit: int
    fin_wait_warn: int
    fin_wait_crit: int
    error_delta_warn: int
    error_delta_crit: int
    probe_success_warn_pct: int
    probe_success_crit_pct: int


@dataclass(frozen=True)
class HopAlert:
    alert_type: str
    alert_key: str
    message: str
    level: str


def evaluate_hop_alerts(
    *,
    host: str,
    links: list[dict[str, Any]],
    error_delta: dict[str, int],
    probes: list[dict[str, Any]],
    thresholds: HopAlertThresholds,
) -> list[HopAlert]:
    alerts: list[HopAlert] = []
    for link in links:
        name = str(link.get("name") or "hop")
        estab = int(link.get("estab", 0) or 0)
        fin_wait = int(link.get("fin_wait", 0) or 0)
        err = str(link.get("error") or "").strip()
        if err:
            alerts.append(
                HopAlert(
                    alert_type="hop_ss_error",
                    alert_key=f"hop_ss_error:{name}",
                    message=f"hop ss error on {host} [{name}]: {err}",
                    level="WARN",
                )
            )
        if thresholds.estab_crit > 0 and estab >= thresholds.estab_crit:
            alerts.append(
                HopAlert(
                    alert_type="hop_estab_high",
                    alert_key=f"hop_estab:{name}",
                    message=f"hop ESTAB CRIT on {host} [{name}]: {estab} >= {thresholds.estab_crit}",
                    level="CRIT",
                )
            )
        elif thresholds.estab_warn > 0 and estab >= thresholds.estab_warn:
            alerts.append(
                HopAlert(
                    alert_type="hop_estab_high",
                    alert_key=f"hop_estab:{name}",
                    message=f"hop ESTAB WARN on {host} [{name}]: {estab} >= {thresholds.estab_warn}",
                    level="WARN",
                )
            )
        if thresholds.fin_wait_crit > 0 and fin_wait >= thresholds.fin_wait_crit:
            alerts.append(
                HopAlert(
                    alert_type="hop_fin_wait_high",
                    alert_key=f"hop_fin_wait:{name}",
                    message=f"hop FIN-WAIT CRIT on {host} [{name}]: {fin_wait} >= {thresholds.fin_wait_crit}",
                    level="CRIT",
                )
            )
        elif thresholds.fin_wait_warn > 0 and fin_wait >= thresholds.fin_wait_warn:
            alerts.append(
                HopAlert(
                    alert_type="hop_fin_wait_high",
                    alert_key=f"hop_fin_wait:{name}",
                    message=f"hop FIN-WAIT WARN on {host} [{name}]: {fin_wait} >= {thresholds.fin_wait_warn}",
                    level="WARN",
                )
            )

    total_err = (
        int(error_delta.get("delta_mux_fail", 0) or 0)
        + int(error_delta.get("delta_conn_refused", 0) or 0)
        + int(error_delta.get("delta_retry_exhausted", 0) or 0)
    )
    if thresholds.error_delta_crit > 0 and total_err >= thresholds.error_delta_crit:
        alerts.append(
            HopAlert(
                alert_type="xray_errors",
                alert_key="xray_errors",
                message=(
                    f"xray error.log CRIT on {host}: {total_err} classified lines "
                    f"(mux={error_delta.get('delta_mux_fail', 0)} "
                    f"refused={error_delta.get('delta_conn_refused', 0)} "
                    f"retry={error_delta.get('delta_retry_exhausted', 0)})"
                ),
                level="CRIT",
            )
        )
    elif thresholds.error_delta_warn > 0 and total_err >= thresholds.error_delta_warn:
        alerts.append(
            HopAlert(
                alert_type="xray_errors",
                alert_key="xray_errors",
                message=(
                    f"xray error.log WARN on {host}: {total_err} classified lines "
                    f"(mux={error_delta.get('delta_mux_fail', 0)} "
                    f"refused={error_delta.get('delta_conn_refused', 0)} "
                    f"retry={error_delta.get('delta_retry_exhausted', 0)})"
                ),
                level="WARN",
            )
        )

    for probe in probes:
        pname = str(probe.get("name") or "probe")
        pct = int(probe.get("success_pct", 0) or 0)
        if thresholds.probe_success_crit_pct > 0 and pct < thresholds.probe_success_crit_pct:
            alerts.append(
                HopAlert(
                    alert_type="probe_degraded",
                    alert_key=f"probe:{pname}",
                    message=(
                        f"hop probe CRIT on {host} [{pname}]: "
                        f"{probe.get('ok', 0)}/{probe.get('total', 0)} ok ({pct}%)"
                    ),
                    level="CRIT",
                )
            )
        elif thresholds.probe_success_warn_pct > 0 and pct < thresholds.probe_success_warn_pct:
            alerts.append(
                HopAlert(
                    alert_type="probe_degraded",
                    alert_key=f"probe:{pname}",
                    message=(
                        f"hop probe WARN on {host} [{pname}]: "
                        f"{probe.get('ok', 0)}/{probe.get('total', 0)} ok ({pct}%)"
                    ),
                    level="WARN",
                )
            )
    return alerts
