"""Trend-based leak alerts for xray RSS/FD growth."""

from __future__ import annotations

import os
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from cock_monitor.config_loader import load_config
from cock_monitor.defaults import DEFAULT_METRICS_DB, DEFAULT_STATE_FILE
from cock_monitor.platform.telegram.client import TelegramClient
from cock_monitor.storage.conntrack_host_repository import ConntrackHostRepository

_MSK_TZ = "Europe/Moscow"


def _as_bool(raw: str, default: bool = False) -> bool:
    s = (raw or "").strip()
    if not s:
        return default
    return s not in {"0", "false", "False", "no", "NO"}


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


def _fmt_moscow_now() -> str:
    prev = os.environ.get("TZ")
    os.environ["TZ"] = _MSK_TZ
    try:
        if hasattr(time, "tzset"):
            time.tzset()
        return time.strftime("%Y-%m-%d %H:%M:%S MSK", time.localtime())
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        if hasattr(time, "tzset"):
            time.tzset()


@dataclass
class LeakAlertConfig:
    enabled: bool
    cooldown_sec: int
    dry_run: bool
    bot_token: str
    chat_id: str
    proxy_url: str | None
    state_file: Path
    metrics_db: Path
    rss_warn_mb: float
    rss_crit_mb: float
    rss_trend_window_hours: int
    rss_trend_min_mb: float
    fds_warn: int
    fds_trend_min: int
    conntrack_fill_warn_pct: int

    @classmethod
    def from_env(cls, raw: dict[str, str], *, dry_run: bool) -> LeakAlertConfig:
        dry_run_cfg = _as_bool(raw.get("DRY_RUN", ""), default=False)
        state = Path(raw.get("STATE_FILE", DEFAULT_STATE_FILE))
        return cls(
            enabled=_as_bool(raw.get("LEAK_ALERT_ENABLE", ""), default=False),
            cooldown_sec=_as_int(raw.get("LEAK_ALERT_COOLDOWN_SEC", ""), 1800),
            dry_run=dry_run or dry_run_cfg,
            bot_token=raw.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=raw.get("TELEGRAM_CHAT_ID", "").strip(),
            proxy_url=raw.get("TELEGRAM_PROXY_URL", "").strip() or None,
            state_file=state.parent / "leak_alert.state",
            metrics_db=Path(raw.get("METRICS_DB", DEFAULT_METRICS_DB)),
            rss_warn_mb=_as_float(raw.get("LEAK_RSS_WARN_MB", ""), 350.0),
            rss_crit_mb=_as_float(raw.get("LEAK_RSS_CRIT_MB", ""), 600.0),
            rss_trend_window_hours=_as_int(raw.get("LEAK_RSS_TREND_WINDOW_HOURS", ""), 6),
            rss_trend_min_mb=_as_float(raw.get("LEAK_RSS_TREND_MIN_MB", ""), 80.0),
            fds_warn=_as_int(raw.get("LEAK_FDS_WARN", ""), 500),
            fds_trend_min=_as_int(raw.get("LEAK_FDS_TREND_MIN", ""), 100),
            conntrack_fill_warn_pct=_as_int(raw.get("LEAK_CONNTRACK_FILL_WARN_PCT", ""), 70),
        )


def _read_last_alert_ts(path: Path) -> int:
    if not path.is_file():
        return 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("last_alert_ts="):
            val = line.split("=", 1)[1].strip()
            if val.isdigit():
                return int(val)
    return 0


def _write_last_alert_ts(path: Path, ts: int, err: TextIO) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".leak_alert.") as tmp:
            tmp.write(f"last_alert_ts={ts}\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except OSError:
        err.write(f"leak_alert: cannot write state {path}\n")


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    den_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


@dataclass(frozen=True)
class LeakAlertVerdict:
    fire: bool
    severity: int
    reason: str


def evaluate_leak_rows(
    rows: list[tuple],
    *,
    cfg: LeakAlertConfig,
    conntrack_rows: list[tuple] | None = None,
) -> LeakAlertVerdict:
    """Analyze host leak rows and optional conntrack fill series."""
    if not rows:
        return LeakAlertVerdict(False, 0, "")

    rss_vals = [float(r[2]) for r in rows if r[2] is not None]
    mem_vals = [float(r[1]) for r in rows if r[1] is not None]
    fds_vals = [float(r[3]) for r in rows if r[3] is not None]

    if not rss_vals:
        return LeakAlertVerdict(False, 0, "")

    latest_rss = rss_vals[-1]
    latest_fds = int(fds_vals[-1]) if fds_vals else 0

    severity = 0
    reasons: list[str] = []

    if latest_rss >= cfg.rss_crit_mb:
        severity = max(severity, 2)
        reasons.append(f"xray RSS {latest_rss:.0f} MB >= crit {cfg.rss_crit_mb:.0f} MB")
    elif latest_rss >= cfg.rss_warn_mb:
        severity = max(severity, 1)
        reasons.append(f"xray RSS {latest_rss:.0f} MB >= warn {cfg.rss_warn_mb:.0f} MB")

    if latest_fds >= cfg.fds_warn:
        severity = max(severity, 1)
        reasons.append(f"xray FDs {latest_fds} >= warn {cfg.fds_warn}")

    window_sec = cfg.rss_trend_window_hours * 3600
    window_rows = [r for r in rows if r[2] is not None and rows[-1][0] - r[0] <= window_sec]
    if len(window_rows) >= 3:
        t0 = window_rows[0][0]
        xs = [float(r[0] - t0) / 3600.0 for r in window_rows]
        rss_series = [float(r[2]) for r in window_rows]
        fds_series = [float(r[3]) for r in window_rows if r[3] is not None]
        rss_growth = rss_series[-1] - rss_series[0]
        if rss_growth >= cfg.rss_trend_min_mb and _pearson(xs, rss_series) > 0.6:
            severity = max(severity, 1)
            reasons.append(
                f"RSS trend +{rss_growth:.0f} MB over {cfg.rss_trend_window_hours}h "
                f"(corr>{0.6:.1f})"
            )
        if fds_series and len(fds_series) >= 3:
            fds_growth = fds_series[-1] - fds_series[0]
            xs_fds = xs[: len(fds_series)]
            if fds_growth >= cfg.fds_trend_min and _pearson(xs_fds, fds_series) > 0.6:
                severity = max(severity, 1)
                reasons.append(f"FD trend +{fds_growth:.0f} over {cfg.rss_trend_window_hours}h")

    if mem_vals and rss_vals and len(mem_vals) == len(rss_vals):
        corr_mem = _pearson(rss_vals, [-m for m in mem_vals])
        rss_delta = rss_vals[-1] - rss_vals[0]
        if corr_mem > 0.7 and rss_delta > 30:
            severity = max(severity, 1)
            reasons.append(f"RSS↑ correlates with MemAvailable↓ (r={corr_mem:.2f})")

    if conntrack_rows:
        fills = [r[1] for r in conntrack_rows if r[1] is not None]
        if fills and fills[-1] >= cfg.conntrack_fill_warn_pct:
            if rss_vals and _pearson(
                [float(f) for f in fills[-len(rss_vals) :]],
                rss_vals[-len(fills) :],
            ) > 0.5:
                severity = max(severity, 1)
                reasons.append(
                    f"conntrack fill {fills[-1]}% correlates with RSS growth"
                )

    if severity == 0:
        return LeakAlertVerdict(False, 0, "")
    return LeakAlertVerdict(True, severity, "; ".join(reasons))


def run_leak_alert(env_file: Path, *, dry_run: bool = False) -> int:
    out = os.sys.stdout
    err = os.sys.stderr
    if not env_file.is_file():
        return 0
    raw = load_config(env_file).app.raw
    cfg = LeakAlertConfig.from_env(raw, dry_run=dry_run)
    if not cfg.enabled:
        return 0
    if not cfg.dry_run and (not cfg.bot_token or not cfg.chat_id):
        err.write("leak_alert: TELEGRAM_* required unless DRY_RUN\n")
        return 1

    now = int(time.time())
    if (now - _read_last_alert_ts(cfg.state_file)) < cfg.cooldown_sec:
        return 0

    window_sec = max(cfg.rss_trend_window_hours, 1) * 3600
    start_ts = now - window_sec

    try:
        with ConntrackHostRepository.open(cfg.metrics_db) as repo:
            rows = repo.fetch_host_leak_rows(start_ts)
            conn = repo._conn
            ct_rows = list(
                conn.execute(
                    """
                    SELECT ts, fill_pct, fill_count
                    FROM conntrack_samples
                    WHERE ts >= ?
                    ORDER BY ts
                    """,
                    (start_ts,),
                ).fetchall()
            )
    except OSError as exc:
        err.write(f"leak_alert: DB read failed: {exc}\n")
        return 1

    verdict = evaluate_leak_rows(rows, cfg=cfg, conntrack_rows=ct_rows)
    if not verdict.fire:
        return 0

    host = socket.getfqdn() or socket.gethostname() or "unknown"
    label = "CRITICAL" if verdict.severity == 2 else "WARNING"
    msg = (
        f"{label} leak trend on {host} ({_fmt_moscow_now()})\n"
        f"{verdict.reason}"
    )
    if cfg.dry_run:
        out.write("[DRY_RUN] Telegram message:\n")
        out.write(msg + "\n")
        return 0

    client = TelegramClient(cfg.bot_token, proxy_url=cfg.proxy_url)
    result = client.send_message_with_result(cfg.chat_id, msg)
    if not result.success:
        err.write(f"leak_alert: telegram failed: {result.reason}\n")
        return 1
    _write_last_alert_ts(cfg.state_file, now, err)
    return 0
