"""Conntrack orchestration use-case (replacement for shell monolith)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB, DEFAULT_STATE_FILE
from cock_monitor.domain.conntrack_policy import metrics_phase_result, severity_from_fill_pct, should_send_fill_alert
from cock_monitor.storage.conntrack_host_repository import (
    ConntrackHostRepository,
    ConntrackSampleInsert,
    HostSampleInsert,
)

_MSK_TZ = "Europe/Moscow"


def _as_int(raw: str, default: int) -> int:
    s = (raw or "").strip()
    if not s:
        return default
    return int(s)


def _as_float(raw: str, default: float) -> float:
    s = (raw or "").strip()
    if not s:
        return default
    return float(s)


def _as_bool(raw: str, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s not in {"0", "false", "False", "no", "NO"}


def _run_cmd(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True)
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def _read_int_file(path: Path) -> int:
    return int(path.read_text(encoding="utf-8", errors="replace").strip())


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_shaper_status(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = _safe_read(path)
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        out[key.strip()] = value.strip().replace("\r", "")
    return out


def _sockstat_field(sockstat_text: str, label: str, key: str) -> int | None:
    for line in sockstat_text.splitlines():
        parts = line.split()
        if not parts or parts[0] != label:
            continue
        for i in range(1, len(parts) - 1, 2):
            if parts[i] == key:
                try:
                    return int(parts[i + 1])
                except ValueError:
                    return None
    return None


def _sum_conntrack_stat(text: str, name: str) -> int:
    total = 0
    for line in text.splitlines():
        for token in line.split():
            if not token.startswith(f"{name}="):
                continue
            _, _, v = token.partition("=")
            if v.isdigit():
                total += int(v)
    return total


def _now_ts() -> int:
    return int(time.time())


def _moscow_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S MSK", time.localtime())


def _fmt_moscow_now() -> str:
    prev = os.environ.get("TZ")
    os.environ["TZ"] = _MSK_TZ
    try:
        if hasattr(time, "tzset"):
            time.tzset()
        return _moscow_time()
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        if hasattr(time, "tzset"):
            time.tzset()


@dataclass
class CheckState:
    fill_last_ts: int = 0
    fill_last_severity: int = 0
    stats_last_ts: int = 0
    la_last_ts: int = 0


@dataclass(frozen=True)
class ConntrackCheckConfig:
    telegram_bot_token: str
    telegram_chat_id: str
    warn_percent: int
    crit_percent: int
    cooldown_seconds: int
    state_file: Path
    check_conntrack_fill: bool
    include_conntrack_stats_line: bool
    dry_run: bool
    alert_on_stats: bool
    stats_drop_min: int
    stats_insert_failed_min: int
    stats_cooldown_seconds: int
    metrics_db: Path
    metrics_record_every_run: bool
    metrics_record_min_interval_sec: int
    metrics_retention_days: int
    metrics_max_rows: int
    alert_on_stats_delta: bool
    stats_delta_min_interval_sec: int
    stats_delta_drop_min: int
    stats_delta_insert_failed_min: int
    stats_delta_early_drop_min: int
    stats_delta_error_min: int
    stats_delta_invalid_min: int
    stats_delta_search_restart_min: int
    stats_rate_drop_per_min: int
    stats_rate_insert_failed_per_min: int
    stats_rate_early_drop_per_min: int
    stats_rate_error_per_min: int
    stats_rate_invalid_per_min: int
    stats_rate_search_restart_per_min: int
    la_alert_enable: bool
    la_warn_threshold: float
    la_alert_cooldown_sec: int
    shaper_status_file: Path
    shaper_iface: str
    metrics_collect_tc_qdisc: bool
    stats_alert_shaper_max_age_min: int

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run_override: bool) -> ConntrackCheckConfig:
        cooldown = _as_int(raw.get("COOLDOWN_SECONDS", ""), 3600)
        dry_run_cfg = _as_bool(raw.get("DRY_RUN", ""), default=False)
        return cls(
            telegram_bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            warn_percent=_as_int(raw.get("WARN_PERCENT", ""), 80),
            crit_percent=_as_int(raw.get("CRIT_PERCENT", ""), 95),
            cooldown_seconds=cooldown,
            state_file=Path(raw.get("STATE_FILE", DEFAULT_STATE_FILE)),
            check_conntrack_fill=_as_bool(raw.get("CHECK_CONNTRACK_FILL", ""), default=True),
            include_conntrack_stats_line=_as_bool(raw.get("INCLUDE_CONNTRACK_STATS_LINE", ""), default=True),
            dry_run=dry_run_cfg or dry_run_override,
            alert_on_stats=_as_bool(raw.get("ALERT_ON_STATS", ""), default=False),
            stats_drop_min=_as_int(raw.get("STATS_DROP_MIN", ""), 0),
            stats_insert_failed_min=_as_int(raw.get("STATS_INSERT_FAILED_MIN", ""), 0),
            stats_cooldown_seconds=_as_int(raw.get("STATS_COOLDOWN_SECONDS", ""), cooldown),
            metrics_db=Path(raw.get("METRICS_DB", DEFAULT_METRICS_DB)),
            metrics_record_every_run=_as_bool(raw.get("METRICS_RECORD_EVERY_RUN", ""), default=True),
            metrics_record_min_interval_sec=_as_int(raw.get("METRICS_RECORD_MIN_INTERVAL_SEC", ""), 0),
            metrics_retention_days=_as_int(raw.get("METRICS_RETENTION_DAYS", ""), 14),
            metrics_max_rows=_as_int(raw.get("METRICS_MAX_ROWS", ""), 0),
            alert_on_stats_delta=_as_bool(raw.get("ALERT_ON_STATS_DELTA", ""), default=False),
            stats_delta_min_interval_sec=_as_int(raw.get("STATS_DELTA_MIN_INTERVAL_SEC", ""), 60),
            stats_delta_drop_min=_as_int(raw.get("STATS_DELTA_DROP_MIN", ""), 0),
            stats_delta_insert_failed_min=_as_int(raw.get("STATS_DELTA_INSERT_FAILED_MIN", ""), 0),
            stats_delta_early_drop_min=_as_int(raw.get("STATS_DELTA_EARLY_DROP_MIN", ""), 0),
            stats_delta_error_min=_as_int(raw.get("STATS_DELTA_ERROR_MIN", ""), 0),
            stats_delta_invalid_min=_as_int(raw.get("STATS_DELTA_INVALID_MIN", ""), 0),
            stats_delta_search_restart_min=_as_int(raw.get("STATS_DELTA_SEARCH_RESTART_MIN", ""), 0),
            stats_rate_drop_per_min=_as_int(raw.get("STATS_RATE_DROP_PER_MIN", ""), 0),
            stats_rate_insert_failed_per_min=_as_int(raw.get("STATS_RATE_INSERT_FAILED_PER_MIN", ""), 0),
            stats_rate_early_drop_per_min=_as_int(raw.get("STATS_RATE_EARLY_DROP_PER_MIN", ""), 0),
            stats_rate_error_per_min=_as_int(raw.get("STATS_RATE_ERROR_PER_MIN", ""), 0),
            stats_rate_invalid_per_min=_as_int(raw.get("STATS_RATE_INVALID_PER_MIN", ""), 0),
            stats_rate_search_restart_per_min=_as_int(raw.get("STATS_RATE_SEARCH_RESTART_PER_MIN", ""), 0),
            la_alert_enable=_as_bool(raw.get("LA_ALERT_ENABLE", ""), default=False),
            la_warn_threshold=_as_float(raw.get("LA_WARN_THRESHOLD", ""), 1.5),
            la_alert_cooldown_sec=_as_int(raw.get("LA_ALERT_COOLDOWN_SEC", ""), 600),
            shaper_status_file=Path(raw.get("SHAPER_STATUS_FILE", "/var/lib/cock-monitor/cpu_shaper.status")),
            shaper_iface=raw.get("SHAPER_IFACE", "ens3").strip() or "ens3",
            metrics_collect_tc_qdisc=_as_bool(raw.get("METRICS_COLLECT_TC_QDISC", ""), default=True),
            stats_alert_shaper_max_age_min=_as_int(raw.get("STATS_ALERT_SHAPER_MAX_AGE_MIN", ""), 15),
        )


class TelegramAdapter:
    def __init__(self, cfg: ConntrackCheckConfig, out: TextIO, err: TextIO) -> None:
        self._cfg = cfg
        self._out = out
        self._err = err

    def send(self, text: str) -> bool:
        if self._cfg.dry_run:
            self._out.write("[DRY_RUN] Telegram message:\n")
            self._out.write(text + "\n")
            return True
        url = f"https://api.telegram.org/bot{self._cfg.telegram_bot_token}/sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": self._cfg.telegram_chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status != 200:
                    self._err.write(f"check-conntrack: Telegram API HTTP {resp.status}\n")
                    return False
        except urllib.error.URLError as exc:
            self._err.write(f"check-conntrack: curl failed: {exc}\n")
            return False
        return True


def _read_state(path: Path) -> CheckState:
    if not path.exists():
        return CheckState()
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = raw_line.partition("=")
        if sep:
            data[key.strip()] = value.strip()
    st = CheckState()
    if data.get("fill_last_ts", "").isdigit():
        st.fill_last_ts = int(data["fill_last_ts"])
    if data.get("fill_last_severity", "") in {"0", "1", "2"}:
        st.fill_last_severity = int(data["fill_last_severity"])
    if data.get("stats_last_ts", "").isdigit():
        st.stats_last_ts = int(data["stats_last_ts"])
    if data.get("la_last_ts", "").isdigit():
        st.la_last_ts = int(data["la_last_ts"])
    return st


def _write_state(path: Path, state: CheckState, err: TextIO) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".state.") as tmp:
            tmp.write(f"fill_last_ts={state.fill_last_ts}\n")
            tmp.write(f"fill_last_severity={state.fill_last_severity}\n")
            tmp.write(f"stats_last_ts={state.stats_last_ts}\n")
            tmp.write(f"la_last_ts={state.la_last_ts}\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except OSError:
        err.write(f"check-conntrack: cannot create state directory {path.parent}\n")


def _conntrack_stats() -> tuple[bool, str]:
    if shutil.which("conntrack") is None:
        return False, ""
    _, out = _run_cmd(["conntrack", "-S"])
    return True, out


def _parse_prev_line(
    line: str | None,
) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None, int | None]:
    if not line:
        return (None, None, None, None, None, None, None)
    parts = line.split("|")
    if len(parts) != 7:
        return (None, None, None, None, None, None, None)

    def _to_int(v: str) -> int | None:
        return int(v) if v.isdigit() else None

    return tuple(_to_int(x) for x in parts)  # type: ignore[return-value]


def _collect_host_sample(cfg: ConntrackCheckConfig, now_ts: int) -> HostSampleInsert:
    load1 = None
    mem_avail = None
    swap_used = None
    tcp_inuse = None
    tcp_orphan = None
    tcp_tw = None
    tcp6_inuse = None
    shaper_rate = None
    shaper_cpu = None
    tc_qdisc_root = None

    loadavg_text = _safe_read(Path("/proc/loadavg"))
    if loadavg_text:
        first = loadavg_text.split()
        if first:
            try:
                load1 = float(first[0])
            except ValueError:
                pass

    meminfo = _safe_read(Path("/proc/meminfo"))
    mi: dict[str, int] = {}
    for line in meminfo.splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            mi[p[0]] = int(p[1])
    mem_avail = mi.get("MemAvailable:")
    st = mi.get("SwapTotal:")
    sf = mi.get("SwapFree:")
    if st is not None and sf is not None:
        swap_used = st - sf

    sock = _safe_read(Path("/proc/net/sockstat"))
    tcp_inuse = _sockstat_field(sock, "TCP:", "inuse")
    tcp_orphan = _sockstat_field(sock, "TCP:", "orphan")
    tcp_tw = _sockstat_field(sock, "TCP:", "tw")
    tcp6_inuse = _sockstat_field(sock, "TCP6:", "inuse")

    shaper = _parse_shaper_status(cfg.shaper_status_file)
    try:
        shaper_rate = float(shaper.get("rate_applied_mbit", ""))
    except ValueError:
        pass
    try:
        shaper_cpu = int(shaper.get("cpu_pct", ""))
    except ValueError:
        pass

    if cfg.metrics_collect_tc_qdisc and shutil.which("tc") is not None:
        _, out = _run_cmd(["tc", "qdisc", "show", "dev", cfg.shaper_iface, "root"])
        first = out.splitlines()[0].strip() if out.splitlines() else ""
        if first:
            tc_qdisc_root = first[:400]

    return HostSampleInsert(
        ts=now_ts,
        load1=load1,
        mem_avail_kb=mem_avail,
        swap_used_kb=swap_used,
        tcp_inuse=tcp_inuse,
        tcp_orphan=tcp_orphan,
        tcp_tw=tcp_tw,
        tcp6_inuse=tcp6_inuse,
        shaper_rate_mbit=shaper_rate,
        shaper_cpu_pct=shaper_cpu,
        tc_qdisc_root=tc_qdisc_root,
    )


def _format_stats_host_context(cfg: ConntrackCheckConfig) -> str:
    loadavg = _safe_read(Path("/proc/loadavg")).split()
    load1 = loadavg[0] if loadavg else "n/a"
    meminfo = _safe_read(Path("/proc/meminfo"))
    mem: dict[str, int] = {}
    for line in meminfo.splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            mem[p[0]] = int(p[1])
    ma = mem.get("MemAvailable:")
    st = mem.get("SwapTotal:")
    sf = mem.get("SwapFree:")
    line1 = f"load1={load1} MemAvailable={ma if ma is not None else 'n/a'}"
    if st is not None and sf is not None:
        line1 += f" kB swap_used={st - sf}/{st} kB"
    else:
        line1 += " swap=n/a"

    sock = _safe_read(Path("/proc/net/sockstat"))
    tcp_line = "sockstat TCP: n/a"
    for line in sock.splitlines():
        if line.startswith("TCP:"):
            tcp_line = line[:220] + ("..." if len(line) > 220 else "")
            break

    shaper = _parse_shaper_status(cfg.shaper_status_file)
    max_min = max(cfg.stats_alert_shaper_max_age_min, 0)
    fresh = False
    if shaper and (shaper.get("rate_applied_mbit") or shaper.get("cpu_pct")):
        if max_min == 0:
            fresh = True
        else:
            ts = shaper.get("ts", "")
            if ts.isdigit() and (_now_ts() - int(ts)) <= max_min * 60:
                fresh = True
    if fresh:
        sh_line = f"shaper: {shaper.get('rate_applied_mbit', '?')} Mbit/s cpu={shaper.get('cpu_pct', '?')}%"
    else:
        sh_line = "shaper: no data"
    return "\n".join([line1, tcp_line, sh_line])


def _read_fill_severity(cfg: ConntrackCheckConfig, err: TextIO) -> tuple[int, int, int] | None:
    cf = Path("/proc/sys/net/netfilter/nf_conntrack_count")
    cm = Path("/proc/sys/net/netfilter/nf_conntrack_max")
    if not cf.exists() or not cm.exists():
        err.write(f"check-conntrack: cannot read {cf} or {cm} (conntrack module enabled?)\n")
        return None
    try:
        count = _read_int_file(cf)
        maxv = _read_int_file(cm)
    except (OSError, ValueError):
        err.write("check-conntrack: unexpected values in nf_conntrack_count/nf_conntrack_max\n")
        return None
    if maxv == 0:
        err.write("check-conntrack: nf_conntrack_max is 0 (conntrack disabled?)\n")
        return None
    pct = (100 * count) // maxv
    return pct, count, maxv


def _metrics_wanted(cfg: ConntrackCheckConfig) -> bool:
    return cfg.metrics_record_every_run or cfg.alert_on_stats_delta


def run_conntrack_check(
    env_file: Path,
    *,
    dry_run_override: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or os.sys.stdout
    err = err or os.sys.stderr
    if not env_file.is_file():
        err.write(f"check-conntrack: config not found: {env_file}\n")
        return 1

    raw_env = load_config(env_file).app.raw
    cfg = ConntrackCheckConfig.from_env(raw_env, dry_run_override=dry_run_override)
    if not cfg.dry_run and (not cfg.telegram_bot_token or not cfg.telegram_chat_id):
        if not cfg.telegram_bot_token:
            err.write("check-conntrack: TELEGRAM_BOT_TOKEN is required unless DRY_RUN=1\n")
            return 1
        err.write("check-conntrack: TELEGRAM_CHAT_ID is required unless DRY_RUN=1\n")
        return 1

    host = socket.getfqdn() or socket.gethostname() or "unknown"
    state = _read_state(cfg.state_file)
    telegram = TelegramAdapter(cfg, out, err)

    fill_pct: int | None = None
    fill_count: int | None = None
    fill_max: int | None = None
    fill_severity = 0

    has_conntrack, conntrack_stats_text = _conntrack_stats()
    drop_sum = _sum_conntrack_stat(conntrack_stats_text, "drop")
    if_sum = _sum_conntrack_stat(conntrack_stats_text, "insert_failed")
    ed_sum = _sum_conntrack_stat(conntrack_stats_text, "early_drop")
    er_sum = _sum_conntrack_stat(conntrack_stats_text, "error")
    inv_sum = _sum_conntrack_stat(conntrack_stats_text, "invalid")
    sr_sum = _sum_conntrack_stat(conntrack_stats_text, "search_restart")

    if cfg.check_conntrack_fill:
        fill = _read_fill_severity(cfg, err)
        if fill is None:
            return 1
        fill_pct, fill_count, fill_max = fill
        fill_severity = severity_from_fill_pct(fill_pct, cfg.warn_percent, cfg.crit_percent)
        stats_note = ""
        if cfg.include_conntrack_stats_line and has_conntrack:
            lines = conntrack_stats_text.splitlines()
            stats_note = lines[0] if lines else ""

        if fill_severity == 0:
            state.fill_last_severity = 0
        else:
            now = _now_ts()
            if should_send_fill_alert(
                fill_severity,
                state.fill_last_ts,
                state.fill_last_severity,
                now,
                cfg.cooldown_seconds,
            ):
                label = "CRITICAL" if fill_severity == 2 else "WARNING"
                msg = (
                    f"{label} conntrack fill on {host} ({_fmt_moscow_now()})\n"
                    f"{fill_pct}% ({fill_count}/{fill_max}) warn>={cfg.warn_percent}% crit>={cfg.crit_percent}%"
                )
                if stats_note:
                    msg += f"\n{stats_note}"
                if not telegram.send(msg):
                    return 1
                state.fill_last_ts = now
                state.fill_last_severity = fill_severity

    now_ts = _now_ts()
    interval_sec = dd = di = de = derr = dinv = dsr = None

    if not cfg.dry_run and _metrics_wanted(cfg):
        cfg.metrics_db.parent.mkdir(parents=True, exist_ok=True)
        try:
            with ConntrackHostRepository.open(cfg.metrics_db) as repo:
                prev = _parse_prev_line(repo.read_last_stats_line())
                p_ts, p_drop, p_if, p_ed, p_er, p_inv, p_sr = prev
                metrics_out = metrics_phase_result(
                    now_ts=now_ts,
                    has_conntrack=has_conntrack,
                    p_ts=p_ts,
                    p_drop=p_drop,
                    p_if=p_if,
                    p_ed=p_ed,
                    p_er=p_er,
                    p_inv=p_inv,
                    p_sr=p_sr,
                    drop_sum=drop_sum,
                    if_sum=if_sum,
                    ed_sum=ed_sum,
                    er_sum=er_sum,
                    inv_sum=inv_sum,
                    sr_sum=sr_sum,
                    alert_on_stats=cfg.alert_on_stats,
                    alert_on_stats_delta=cfg.alert_on_stats_delta,
                    stats_last_ts=state.stats_last_ts,
                    stats_cooldown_seconds=cfg.stats_cooldown_seconds,
                    stats_drop_min=cfg.stats_drop_min,
                    stats_insert_failed_min=cfg.stats_insert_failed_min,
                    stats_delta_min_interval_sec=cfg.stats_delta_min_interval_sec,
                    stats_delta_drop_min=cfg.stats_delta_drop_min,
                    stats_delta_insert_failed_min=cfg.stats_delta_insert_failed_min,
                    stats_delta_early_drop_min=cfg.stats_delta_early_drop_min,
                    stats_delta_error_min=cfg.stats_delta_error_min,
                    stats_delta_invalid_min=cfg.stats_delta_invalid_min,
                    stats_delta_search_restart_min=cfg.stats_delta_search_restart_min,
                    stats_rate_drop_per_min=cfg.stats_rate_drop_per_min,
                    stats_rate_insert_failed_per_min=cfg.stats_rate_insert_failed_per_min,
                    stats_rate_early_drop_per_min=cfg.stats_rate_early_drop_per_min,
                    stats_rate_error_per_min=cfg.stats_rate_error_per_min,
                    stats_rate_invalid_per_min=cfg.stats_rate_invalid_per_min,
                    stats_rate_search_restart_per_min=cfg.stats_rate_search_restart_per_min,
                )
                interval_sec = metrics_out["interval_sec"]
                dd = metrics_out["dd"]
                di = metrics_out["di"]
                de = metrics_out["de"]
                derr = metrics_out["derr"]
                dinv = metrics_out["dinv"]
                dsr = metrics_out["dsr"]

                do_insert = cfg.metrics_record_every_run or cfg.alert_on_stats_delta
                if do_insert and cfg.metrics_record_min_interval_sec > 0 and p_ts is not None:
                    if (now_ts - p_ts) < cfg.metrics_record_min_interval_sec:
                        do_insert = False

                if do_insert:
                    sample = ConntrackSampleInsert(
                        ts=now_ts,
                        fill_pct=fill_pct,
                        fill_count=fill_count,
                        fill_max=fill_max,
                        drop=drop_sum if has_conntrack else 0,
                        insert_failed=if_sum if has_conntrack else 0,
                        early_drop=ed_sum if has_conntrack else 0,
                        error=er_sum if has_conntrack else 0,
                        invalid=inv_sum if has_conntrack else 0,
                        search_restart=sr_sum if has_conntrack else 0,
                        interval_sec=interval_sec if has_conntrack else None,
                        delta_drop=dd if has_conntrack else None,
                        delta_insert_failed=di if has_conntrack else None,
                        delta_early_drop=de if has_conntrack else None,
                        delta_error=derr if has_conntrack else None,
                        delta_invalid=dinv if has_conntrack else None,
                        delta_search_restart=dsr if has_conntrack else None,
                    )
                    repo.insert_sample_and_host(sample, _collect_host_sample(cfg, now_ts))
                    if cfg.metrics_retention_days > 0:
                        repo.apply_retention(_now_ts() - cfg.metrics_retention_days * 86400)
                    if cfg.metrics_max_rows > 0:
                        repo.trim_to_max_rows(cfg.metrics_max_rows)
                    repo.delete_host_orphans()
        except OSError as exc:
            err.write(f"check-conntrack: metrics DB write failed: {exc}\n")
            return 1
    else:
        metrics_out = metrics_phase_result(
            now_ts=now_ts,
            has_conntrack=has_conntrack,
            p_ts=None,
            p_drop=None,
            p_if=None,
            p_ed=None,
            p_er=None,
            p_inv=None,
            p_sr=None,
            drop_sum=drop_sum,
            if_sum=if_sum,
            ed_sum=ed_sum,
            er_sum=er_sum,
            inv_sum=inv_sum,
            sr_sum=sr_sum,
            alert_on_stats=cfg.alert_on_stats,
            alert_on_stats_delta=cfg.alert_on_stats_delta,
            stats_last_ts=state.stats_last_ts,
            stats_cooldown_seconds=cfg.stats_cooldown_seconds,
            stats_drop_min=cfg.stats_drop_min,
            stats_insert_failed_min=cfg.stats_insert_failed_min,
            stats_delta_min_interval_sec=cfg.stats_delta_min_interval_sec,
            stats_delta_drop_min=cfg.stats_delta_drop_min,
            stats_delta_insert_failed_min=cfg.stats_delta_insert_failed_min,
            stats_delta_early_drop_min=cfg.stats_delta_early_drop_min,
            stats_delta_error_min=cfg.stats_delta_error_min,
            stats_delta_invalid_min=cfg.stats_delta_invalid_min,
            stats_delta_search_restart_min=cfg.stats_delta_search_restart_min,
            stats_rate_drop_per_min=cfg.stats_rate_drop_per_min,
            stats_rate_insert_failed_per_min=cfg.stats_rate_insert_failed_per_min,
            stats_rate_early_drop_per_min=cfg.stats_rate_early_drop_per_min,
            stats_rate_error_per_min=cfg.stats_rate_error_per_min,
            stats_rate_invalid_per_min=cfg.stats_rate_invalid_per_min,
            stats_rate_search_restart_per_min=cfg.stats_rate_search_restart_per_min,
        )

    if has_conntrack and bool(metrics_out["stats_fire"]):
        reason = str(metrics_out["stats_reason"] or "")
        stats_line = conntrack_stats_text.splitlines()[0] if conntrack_stats_text.splitlines() else ""
        msg = f"STATS {host} ({_fmt_moscow_now()})\n{reason}\n{stats_line}\n{_format_stats_host_context(cfg)}"
        if bool(metrics_out["stats_send_telegram"]):
            if not telegram.send(msg):
                return 1
            state.stats_last_ts = _now_ts()

    if cfg.la_alert_enable:
        la_text = _safe_read(Path("/proc/loadavg")).split()
        la1 = la_text[0] if la_text else "0"
        try:
            la1_f = float(la1)
        except ValueError:
            la1_f = 0.0
        if la1_f >= cfg.la_warn_threshold:
            now_la = _now_ts()
            if (now_la - state.la_last_ts) >= cfg.la_alert_cooldown_sec:
                ncpu_code, ncpu_out = _run_cmd(["nproc"])
                ncpu = ncpu_out.strip() if ncpu_code == 0 and ncpu_out.strip() else "?"
                shaper = _parse_shaper_status(cfg.shaper_status_file)
                s_cpu = shaper.get("cpu_pct", "?")
                s_rate = shaper.get("rate_applied_mbit", "?")
                s_op = shaper.get("tc_op", "hold")
                s_iface = shaper.get("iface", cfg.shaper_iface)
                op_label = "стабильно"
                if s_op == "step_down":
                    op_label = "ограничение ↓"
                if s_op == "step_up":
                    op_label = "восстановление ↑"
                tx_mbit = "?"
                rx_mbit = "?"
                net_text_1 = _safe_read(Path("/proc/net/dev"))
                if net_text_1:
                    rx1 = tx1 = rx2 = tx2 = None
                    for line in net_text_1.splitlines():
                        if line.strip().startswith(f"{s_iface}:"):
                            p = line.replace(":", " ").split()
                            if len(p) >= 11:
                                rx1 = p[1]
                                tx1 = p[9]
                            break
                    time.sleep(1)
                    net_text_2 = _safe_read(Path("/proc/net/dev"))
                    for line in net_text_2.splitlines():
                        if line.strip().startswith(f"{s_iface}:"):
                            p = line.replace(":", " ").split()
                            if len(p) >= 11:
                                rx2 = p[1]
                                tx2 = p[9]
                            break
                    if rx1 and rx2 and rx1.isdigit() and rx2.isdigit() and int(rx2) >= int(rx1):
                        rx_mbit = str((int(rx2) - int(rx1)) * 8 // 1_000_000)
                    if tx1 and tx2 and tx1.isdigit() and tx2.isdigit() and int(tx2) >= int(tx1):
                        tx_mbit = str((int(tx2) - int(tx1)) * 8 // 1_000_000)
                la_msg = (
                    f"⚠️ High Load Average on {host} ({_fmt_moscow_now()})\n"
                    f"la1={la1} (порог: >={cfg.la_warn_threshold}, vCPU: {ncpu})\n"
                    f"CPU: {s_cpu}% | Шейпер: {op_label} @ {s_rate} Mbit/s\n"
                    f"Трафик {s_iface}: исходящий (к клиентам) {tx_mbit} Mbit/s | "
                    f"входящий (от клиентов) {rx_mbit} Mbit/s"
                )
                if not telegram.send(la_msg):
                    return 1
                state.la_last_ts = now_la

    _write_state(cfg.state_file, state, err)
    return 0
