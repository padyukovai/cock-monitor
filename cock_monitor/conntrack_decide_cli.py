"""stdin JSON -> stdout JSON or bash `declare` lines (for check-conntrack.sh)."""

from __future__ import annotations

import json
import shlex
import sys
from typing import Any


def _as_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x != 0
    if isinstance(x, str):
        return x not in ("", "0", "false", "False", "no", "NO")
    return False


def _shell_declare(name: str, value: Any) -> str:
    if value is None:
        return f"declare -- {name}="
    if isinstance(value, bool):
        return f"declare -- {name}={int(value)}"
    if isinstance(value, int):
        return f"declare -- {name}={value}"
    if isinstance(value, str):
        return f"declare -- {name}={shlex.quote(value)}"
    raise TypeError(f"unsupported value for {name}: {type(value)!r}")


def _emit_shell(d: dict[str, Any]) -> None:
    for k, v in d.items():
        print(_shell_declare(k, v))


def run(argv: list[str]) -> int:
    use_shell = "--shell" in argv
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"conntrack-decide: invalid JSON: {e}", file=sys.stderr)
        return 2

    phase = payload.get("phase")
    if phase == "fill":
        from cock_monitor.domain.conntrack_policy import should_send_fill_alert

        out = {
            "fill_should_send": should_send_fill_alert(
                int(payload["fill_severity"]),
                payload.get("fill_last_ts", 0),
                payload.get("fill_last_severity", 0),
                int(payload["now_ts"]),
                int(payload["cooldown_seconds"]),
            )
        }
        if use_shell:
            _emit_shell({"fill_should_send": int(out["fill_should_send"])})
        else:
            print(json.dumps(out, ensure_ascii=False))
        return 0

    if phase == "metrics":
        from cock_monitor.domain.conntrack_policy import metrics_phase_result

        has_ct = _as_bool(payload.get("has_conntrack", payload.get("has_ct", False)))
        out = metrics_phase_result(
            now_ts=int(payload["now_ts"]),
            has_conntrack=has_ct,
            p_ts=payload.get("p_ts"),
            p_drop=payload.get("p_drop"),
            p_if=payload.get("p_if"),
            p_ed=payload.get("p_ed"),
            p_er=payload.get("p_er"),
            p_inv=payload.get("p_inv"),
            p_sr=payload.get("p_sr"),
            drop_sum=int(payload.get("drop_sum", 0)),
            if_sum=int(payload.get("if_sum", 0)),
            ed_sum=int(payload.get("ed_sum", 0)),
            er_sum=int(payload.get("er_sum", 0)),
            inv_sum=int(payload.get("inv_sum", 0)),
            sr_sum=int(payload.get("sr_sum", 0)),
            alert_on_stats=_as_bool(payload.get("alert_on_stats")),
            alert_on_stats_delta=_as_bool(payload.get("alert_on_stats_delta")),
            stats_last_ts=payload.get("stats_last_ts", 0),
            stats_cooldown_seconds=int(payload.get("stats_cooldown_seconds", 3600)),
            stats_drop_min=payload.get("stats_drop_min", 0),
            stats_insert_failed_min=payload.get("stats_insert_failed_min", 0),
            stats_delta_min_interval_sec=payload.get("stats_delta_min_interval_sec", 60),
            stats_delta_drop_min=payload.get("stats_delta_drop_min", 0),
            stats_delta_insert_failed_min=payload.get("stats_delta_insert_failed_min", 0),
            stats_delta_early_drop_min=payload.get("stats_delta_early_drop_min", 0),
            stats_delta_error_min=payload.get("stats_delta_error_min", 0),
            stats_delta_invalid_min=payload.get("stats_delta_invalid_min", 0),
            stats_delta_search_restart_min=payload.get("stats_delta_search_restart_min", 0),
            stats_rate_drop_per_min=payload.get("stats_rate_drop_per_min", 0),
            stats_rate_insert_failed_per_min=payload.get("stats_rate_insert_failed_per_min", 0),
            stats_rate_early_drop_per_min=payload.get("stats_rate_early_drop_per_min", 0),
            stats_rate_error_per_min=payload.get("stats_rate_error_per_min", 0),
            stats_rate_invalid_per_min=payload.get("stats_rate_invalid_per_min", 0),
            stats_rate_search_restart_per_min=payload.get("stats_rate_search_restart_per_min", 0),
        )
        if use_shell:
            shell_out: dict[str, Any] = {
                "stats_fire": int(bool(out["stats_fire"])),
                "stats_send_telegram": int(bool(out["stats_send_telegram"])),
                "stats_reason": out["stats_reason"] or "",
            }
            for k in ("interval_sec", "dd", "di", "de", "derr", "dinv", "dsr"):
                v = out[k]
                if v is None:
                    shell_out[k] = ""
                else:
                    shell_out[k] = int(v)
            _emit_shell(shell_out)
        else:
            print(json.dumps(out, ensure_ascii=False))
        return 0

    print("conntrack-decide: unknown phase (use fill|metrics)", file=sys.stderr)
    return 2
