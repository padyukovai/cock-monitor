from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cock_monitor.env import parse_env_file


class StatusReportError(RuntimeError):
    """Raised when status report cannot be built with current environment."""


def _as_int(raw: str, default: int) -> int:
    s = (raw or "").strip()
    if not s:
        return default
    return int(s)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _run_readonly_cmd(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True)
    except OSError:
        return 127, ""
    return proc.returncode, (proc.stdout or "")


def _meminfo_kb(text: str, key: str) -> int | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == key and parts[1].isdigit():
            return int(parts[1])
    return None


def _sum_conntrack_stat(text: str, name: str) -> int:
    total = 0
    for line in text.splitlines():
        for token in line.split():
            if not token.startswith(f"{name}="):
                continue
            _, _, value = token.partition("=")
            if value.isdigit():
                total += int(value)
    return total


def _parse_shaper_status(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _safe_read(path).splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().replace("\r", "")
    return out


@dataclass(frozen=True)
class StatusConfig:
    warn_percent: int
    crit_percent: int
    check_conntrack_fill: bool
    include_conntrack_stats_line: bool
    alert_on_stats: bool
    alert_on_stats_delta: bool
    stats_drop_min: int
    stats_insert_failed_min: int
    stats_cooldown_seconds: int
    metrics_db: Path
    metrics_record_every_run: bool
    metrics_retention_days: int
    shaper_status_file: Path
    shaper_iface: str
    status_wan_iface: str
    status_ip_link_head_lines: int
    status_extra_units: list[str]

    @classmethod
    def from_env_map(cls, env: dict[str, str]) -> StatusConfig:
        def _flag(name: str, default: bool) -> bool:
            raw = (env.get(name, "") or "").strip()
            if not raw:
                return default
            return raw not in {"0", "false", "False", "no", "NO"}

        shaper_iface = (env.get("SHAPER_IFACE", "") or "").strip() or "ens3"
        wan_iface = (env.get("STATUS_WAN_IFACE", "") or "").strip() or shaper_iface
        ip_lines = _as_int(env.get("STATUS_IP_LINK_HEAD_LINES", ""), 22)
        ip_lines = max(8, min(60, ip_lines))
        extra_units_raw = (env.get("STATUS_EXTRA_UNITS", "") or "").strip()
        extra_units = [u for u in extra_units_raw.split() if u]
        return cls(
            warn_percent=_as_int(env.get("WARN_PERCENT", ""), 80),
            crit_percent=_as_int(env.get("CRIT_PERCENT", ""), 95),
            check_conntrack_fill=_flag("CHECK_CONNTRACK_FILL", True),
            include_conntrack_stats_line=_flag("INCLUDE_CONNTRACK_STATS_LINE", True),
            alert_on_stats=_flag("ALERT_ON_STATS", False),
            alert_on_stats_delta=_flag("ALERT_ON_STATS_DELTA", False),
            stats_drop_min=_as_int(env.get("STATS_DROP_MIN", ""), 0),
            stats_insert_failed_min=_as_int(env.get("STATS_INSERT_FAILED_MIN", ""), 0),
            stats_cooldown_seconds=_as_int(env.get("STATS_COOLDOWN_SECONDS", ""), 3600),
            metrics_db=Path(env.get("METRICS_DB", "/var/lib/cock-monitor/metrics.db")),
            metrics_record_every_run=_flag("METRICS_RECORD_EVERY_RUN", True),
            metrics_retention_days=_as_int(env.get("METRICS_RETENTION_DAYS", ""), 14),
            shaper_status_file=Path(
                env.get("SHAPER_STATUS_FILE", "/var/lib/cock-monitor/cpu_shaper.status")
            ),
            shaper_iface=shaper_iface,
            status_wan_iface=wan_iface,
            status_ip_link_head_lines=ip_lines,
            status_extra_units=extra_units,
        )


def _fill_snapshot(cfg: StatusConfig) -> tuple[int, int, int, int]:
    count_path = Path("/proc/sys/net/netfilter/nf_conntrack_count")
    max_path = Path("/proc/sys/net/netfilter/nf_conntrack_max")
    if not count_path.exists() or not max_path.exists():
        raise StatusReportError(f"cannot read {count_path} or {max_path} (conntrack module enabled?)")
    try:
        count = int(count_path.read_text(encoding="utf-8", errors="replace").strip())
        maxv = int(max_path.read_text(encoding="utf-8", errors="replace").strip())
    except (OSError, ValueError) as exc:
        raise StatusReportError("unexpected values in nf_conntrack_count/nf_conntrack_max") from exc
    if maxv == 0:
        raise StatusReportError("nf_conntrack_max is 0 (conntrack disabled?)")
    pct = (100 * count) // maxv
    severity = 2 if pct >= cfg.crit_percent else 1 if pct >= cfg.warn_percent else 0
    return pct, count, maxv, severity


def build_status_report(env_file: Path) -> str:
    env_file = env_file.expanduser().resolve()
    if not env_file.is_file():
        raise StatusReportError(f"config not found: {env_file}")

    raw_env = parse_env_file(env_file)
    cfg = StatusConfig.from_env_map(raw_env)
    host = socket.getfqdn() or socket.gethostname() or "unknown"
    now_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M:%S MSK")

    lines: list[str] = [f"time: {now_msk}", f"host: {host}", "", "--- Host snapshot ---"]

    meminfo = _safe_read(Path("/proc/meminfo"))
    if meminfo:
        mem_available = _meminfo_kb(meminfo, "MemAvailable:")
        swap_total = _meminfo_kb(meminfo, "SwapTotal:")
        swap_free = _meminfo_kb(meminfo, "SwapFree:")
        mem_line = f"mem: MemAvailable={mem_available if mem_available is not None else '(n/a)'} kB"
        if swap_total is not None and swap_free is not None:
            mem_line += f" | swap used={swap_total - swap_free}/{swap_total} kB (free {swap_free} kB)"
        lines.append(mem_line)
    else:
        lines.append("mem: (/proc/meminfo not readable)")

    loadavg = _safe_read(Path("/proc/loadavg")).strip()
    lines.append(f"loadavg: {loadavg if loadavg else '(/proc/loadavg not readable)'}")

    sockstat = _safe_read(Path("/proc/net/sockstat"))
    if sockstat:
        lines.append("sockstat:")
        tcp_lines = [line for line in sockstat.splitlines() if line.startswith(("TCP:", "TCP6:"))][:4]
        if tcp_lines:
            lines.extend(tcp_lines)
        else:
            lines.append("(no TCP lines)")
    else:
        lines.append("sockstat: (/proc/net/sockstat not readable)")

    lines.append("")
    lines.append(
        f"WAN iface {cfg.status_wan_iface} (ip -s link, first {cfg.status_ip_link_head_lines} lines):"
    )
    ip_cmd = shutil.which("ip")
    if not ip_cmd:
        lines.append("(ip command not found)")
    else:
        rc_check, _ = _run_readonly_cmd([ip_cmd, "link", "show", "dev", cfg.status_wan_iface])
        if rc_check != 0:
            lines.append("(interface not found)")
        else:
            _, ip_out = _run_readonly_cmd([ip_cmd, "-s", "link", "show", "dev", cfg.status_wan_iface])
            ip_lines = ip_out.splitlines()[: cfg.status_ip_link_head_lines]
            if ip_lines:
                lines.extend(ip_lines)
            else:
                lines.append("(ip -s link failed)")

    if cfg.status_extra_units:
        lines.append("")
        lines.append("extra units (STATUS_EXTRA_UNITS):")
        systemctl = shutil.which("systemctl")
        for unit in cfg.status_extra_units:
            if not systemctl:
                lines.append(f"  {unit}: (systemctl not found)")
                continue
            _, active_out = _run_readonly_cmd([systemctl, "is-active", unit])
            _, ts_out = _run_readonly_cmd([systemctl, "show", unit, "-p", "ActiveEnterTimestamp", "--value"])
            active = active_out.strip() or "?"
            active_enter = ts_out.strip() or "?"
            lines.append(f"  {unit}: {active} | ActiveEnter={active_enter}")

    lines.append("")
    if cfg.check_conntrack_fill:
        fill_pct, fill_count, fill_max, fill_level = _fill_snapshot(cfg)
        lines.append(f"conntrack fill: {fill_pct}% ({fill_count}/{fill_max})")
        level_text = {0: "OK", 1: "WARNING", 2: "CRITICAL"}.get(fill_level, "unknown")
        lines.append(f"level: {level_text} (warn>={cfg.warn_percent}% crit>={cfg.crit_percent}%)")
    else:
        lines.append("conntrack fill check: disabled (CHECK_CONNTRACK_FILL=0)")

    lines.append("")
    lines.append(f"INCLUDE_CONNTRACK_STATS_LINE={1 if cfg.include_conntrack_stats_line else 0}")

    conntrack = shutil.which("conntrack")
    conntrack_out = ""
    if not conntrack:
        lines.append("")
        lines.append("conntrack: command not found")
    else:
        _, conntrack_out = _run_readonly_cmd([conntrack, "-S"])
        lines.append("")
        lines.append("conntrack -S:")
        lines.extend(conntrack_out.splitlines() or ["(conntrack -S failed)"])

    lines.append("")
    lines.append(
        f"stats alerts: ALERT_ON_STATS={1 if cfg.alert_on_stats else 0} "
        f"ALERT_ON_STATS_DELTA={1 if cfg.alert_on_stats_delta else 0}"
    )
    lines.append(
        f"STATS_DROP_MIN={cfg.stats_drop_min} STATS_INSERT_FAILED_MIN={cfg.stats_insert_failed_min} "
        f"STATS_COOLDOWN_SECONDS={cfg.stats_cooldown_seconds}"
    )
    lines.append(
        f"METRICS_DB={cfg.metrics_db} METRICS_RECORD_EVERY_RUN={1 if cfg.metrics_record_every_run else 0} "
        f"METRICS_RETENTION_DAYS={cfg.metrics_retention_days}"
    )
    if conntrack_out:
        lines.append(
            "current sums: "
            f"drop={_sum_conntrack_stat(conntrack_out, 'drop')} "
            f"insert_failed={_sum_conntrack_stat(conntrack_out, 'insert_failed')} "
            f"early_drop={_sum_conntrack_stat(conntrack_out, 'early_drop')} "
            f"error={_sum_conntrack_stat(conntrack_out, 'error')} "
            f"invalid={_sum_conntrack_stat(conntrack_out, 'invalid')} "
            f"search_restart={_sum_conntrack_stat(conntrack_out, 'search_restart')}"
        )

    shaper = _parse_shaper_status(cfg.shaper_status_file)
    lines.append("")
    lines.append("--- VPN CPU Shaper ---")
    if shaper:
        op = shaper.get("tc_op", "hold")
        emoji = "🟢"
        op_rus = "стабильно (hold)"
        if op == "step_down":
            emoji = "🔴"
            op_rus = "ограничение (step_down)"
        elif op == "step_up":
            emoji = "🟡"
            op_rus = "ускорение (step_up)"
        else:
            cpu = shaper.get("cpu_pct", "")
            if cpu.isdigit() and int(cpu) > 80:
                emoji = "🟠"
        lines.append(
            f"{emoji} Скорость VPN: {shaper.get('rate_applied_mbit', '?')} Mbit/s "
            f"(на {shaper.get('iface', cfg.shaper_iface)})"
        )
        lines.append(f"   Действие: {op_rus}")
        lines.append(f"   Загрузка CPU: {shaper.get('cpu_pct', '?')}%")
        ts = shaper.get("ts", "")
        if ts.isdigit():
            stale_sec = int(time.time()) - int(ts)
            if stale_sec > 300:
                lines.append(f"   ⚠️ Данные устарели на {stale_sec} сек.")
    else:
        lines.append("Отключен или нет данных")

    return "\n".join(lines).strip()
