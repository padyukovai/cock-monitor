"""Conntrack fill / STATS alert policy (ported from bin/check-conntrack.sh)."""

from __future__ import annotations

U32_MOD = 2**32


def _nonneg_int_str(s: object) -> int | None:
    if s is None:
        return None
    if isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s if s >= 0 else None
    if isinstance(s, str) and s.isdigit():
        return int(s)
    return None


def u32_counter_delta(old: object, new: object) -> int | None:
    """Unsigned 32-bit counter delta; None if inputs invalid (matches empty bash output)."""
    o = _nonneg_int_str(old)
    n = _nonneg_int_str(new)
    if o is None or n is None:
        return None
    if n >= o:
        return n - o
    return U32_MOD - o + n


def severity_from_fill_pct(pct: int, warn_percent: int, crit_percent: int) -> int:
    """0 ok, 1 warning, 2 critical (same thresholds as compute_fill_severity in shell)."""
    if pct >= crit_percent:
        return 2
    if pct >= warn_percent:
        return 1
    return 0


def _norm_fill_ts(ts: object) -> int:
    v = _nonneg_int_str(ts)
    return v if v is not None else 0


def _norm_fill_sev(sev: object) -> int:
    if isinstance(sev, int) and 0 <= sev <= 2:
        return sev
    if isinstance(sev, str) and len(sev) == 1 and sev in "012":
        return int(sev)
    return 0


def should_send_fill_alert(
    current: int,
    prev_ts: object,
    prev_severity: object,
    now: int,
    cooldown_seconds: int,
) -> bool:
    """Return True if a fill Telegram alert should be sent (bash should_send_fill_alert)."""
    ts_prev = _norm_fill_ts(prev_ts)
    sev_prev = _norm_fill_sev(prev_severity)
    if current == 0:
        return False
    if current > sev_prev:
        return True
    if sev_prev == 0:
        return True
    return (now - ts_prev) >= cooldown_seconds


def should_send_stats_alert(prev_ts: object, now: int, stats_cooldown_seconds: int) -> bool:
    """Bash should_send_stats_alert: send if cooldown elapsed."""
    ts_prev = _norm_fill_ts(prev_ts)
    return (now - ts_prev) >= stats_cooldown_seconds


def compute_interval_and_deltas(
    *,
    now_ts: int,
    has_conntrack: bool,
    p_ts: object,
    p_drop: object,
    p_if: object,
    p_ed: object,
    p_er: object,
    p_inv: object,
    p_sr: object,
    drop_sum: int,
    if_sum: int,
    ed_sum: int,
    er_sum: int,
    inv_sum: int,
    sr_sum: int,
) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None, int | None]:
    """
    Reproduce bash block: interval_sec and dd..dsr (None => empty string / SQL NULL in shell).
    """
    p_ts_i = _nonneg_int_str(p_ts)
    if not has_conntrack or p_ts_i is None:
        return (None, None, None, None, None, None, None)
    interval_sec = now_ts - p_ts_i
    if interval_sec <= 0:
        return (None, None, None, None, None, None, None)

    def nz(x: object) -> int:
        v = _nonneg_int_str(x)
        return v if v is not None else 0

    pd, pi, ped, per, pinv, psr = (
        nz(p_drop),
        nz(p_if),
        nz(p_ed),
        nz(p_er),
        nz(p_inv),
        nz(p_sr),
    )
    dd = u32_counter_delta(pd, drop_sum)
    di = u32_counter_delta(pi, if_sum)
    de = u32_counter_delta(ped, ed_sum)
    derr = u32_counter_delta(per, er_sum)
    dinv = u32_counter_delta(pinv, inv_sum)
    dsr = u32_counter_delta(psr, sr_sum)
    return (interval_sec, dd, di, de, derr, dinv, dsr)


def _positive_int(x: object) -> int | None:
    v = _nonneg_int_str(x)
    if v is None or v <= 0:
        return None
    return v


def evaluate_stats_alert(
    *,
    has_conntrack: bool,
    alert_on_stats: bool,
    alert_on_stats_delta: bool,
    interval_sec: int | None,
    dd: int | None,
    di: int | None,
    de: int | None,
    derr: int | None,
    dinv: int | None,
    dsr: int | None,
    drop_sum: int,
    if_sum: int,
    ed_sum: int,
    er_sum: int,
    inv_sum: int,
    sr_sum: int,
    stats_drop_min: object,
    stats_insert_failed_min: object,
    stats_delta_min_interval_sec: object,
    stats_delta_drop_min: object,
    stats_delta_insert_failed_min: object,
    stats_delta_early_drop_min: object,
    stats_delta_error_min: object,
    stats_delta_invalid_min: object,
    stats_delta_search_restart_min: object,
    stats_rate_drop_per_min: object,
    stats_rate_insert_failed_per_min: object,
    stats_rate_early_drop_per_min: object,
    stats_rate_error_per_min: object,
    stats_rate_invalid_per_min: object,
    stats_rate_search_restart_per_min: object,
) -> tuple[bool, str]:
    """
    STATS cumulative + delta/rate (bash lines 475–537).
    Returns (stats_fire, stats_reason).
    """
    if not has_conntrack:
        return (False, "")

    stats_fire = False
    parts: list[str] = []

    if alert_on_stats:
        sdm = _positive_int(stats_drop_min)
        if sdm is not None and drop_sum >= sdm:
            stats_fire = True
            parts.append(f"cumulative: drop={drop_sum} (>={sdm})")
        sifm = _positive_int(stats_insert_failed_min)
        if sifm is not None and if_sum >= sifm:
            stats_fire = True
            parts.append(f"cumulative: insert_failed={if_sum} (>={sifm})")

    sd_interval = _nonneg_int_str(stats_delta_min_interval_sec)
    if sd_interval is None:
        sd_interval = 60

    delta_ok = (
        alert_on_stats_delta
        and interval_sec is not None
        and interval_sec > 0
        and interval_sec >= sd_interval
    )

    dpart = False
    if delta_ok:
        iv = interval_sec
        assert iv is not None and iv > 0

        def rate_per_min(d: int | None) -> int:
            if d is None:
                return 0
            return d * 60 // iv

        rd = rate_per_min(dd)
        ri = rate_per_min(di)
        re = rate_per_min(de)
        rerr = rate_per_min(derr)
        rin = rate_per_min(dinv)
        rsr = rate_per_min(dsr)

        def check_delta(d: int | None, thresh: object) -> bool:
            t = _positive_int(thresh)
            return t is not None and d is not None and d >= t

        def check_rate(r: int, thresh: object) -> bool:
            t = _positive_int(thresh)
            return t is not None and r >= t

        if check_delta(dd, stats_delta_drop_min):
            dpart = True
        if check_rate(rd, stats_rate_drop_per_min):
            dpart = True
        if check_delta(di, stats_delta_insert_failed_min):
            dpart = True
        if check_rate(ri, stats_rate_insert_failed_per_min):
            dpart = True
        if check_delta(de, stats_delta_early_drop_min):
            dpart = True
        if check_rate(re, stats_rate_early_drop_per_min):
            dpart = True
        if check_delta(derr, stats_delta_error_min):
            dpart = True
        if check_rate(rerr, stats_rate_error_per_min):
            dpart = True
        if check_delta(dinv, stats_delta_invalid_min):
            dpart = True
        if check_rate(rin, stats_rate_invalid_per_min):
            dpart = True
        if check_delta(dsr, stats_delta_search_restart_min):
            dpart = True
        if check_rate(rsr, stats_rate_search_restart_per_min):
            dpart = True

        if dpart:
            stats_fire = True
            def fmt_d(v: int | None) -> str:
                return str(v) if v is not None else "?"

            parts.append(
                f"delta ({iv}s): drop+{fmt_d(dd)} (~{rd}/min) insert_failed+{fmt_d(di)} "
                f"early_drop+{fmt_d(de)} error+{fmt_d(derr)} invalid+{fmt_d(dinv)} search_restart+{fmt_d(dsr)}"
            )

    sep = "; "
    return (stats_fire, sep.join(parts))


def metrics_phase_result(
    *,
    now_ts: int,
    has_conntrack: bool,
    p_ts: object,
    p_drop: object,
    p_if: object,
    p_ed: object,
    p_er: object,
    p_inv: object,
    p_sr: object,
    drop_sum: int,
    if_sum: int,
    ed_sum: int,
    er_sum: int,
    inv_sum: int,
    sr_sum: int,
    alert_on_stats: bool,
    alert_on_stats_delta: bool,
    stats_last_ts: object,
    stats_cooldown_seconds: int,
    stats_drop_min: object,
    stats_insert_failed_min: object,
    stats_delta_min_interval_sec: object,
    stats_delta_drop_min: object,
    stats_delta_insert_failed_min: object,
    stats_delta_early_drop_min: object,
    stats_delta_error_min: object,
    stats_delta_invalid_min: object,
    stats_delta_search_restart_min: object,
    stats_rate_drop_per_min: object,
    stats_rate_insert_failed_per_min: object,
    stats_rate_early_drop_per_min: object,
    stats_rate_error_per_min: object,
    stats_rate_invalid_per_min: object,
    stats_rate_search_restart_per_min: object,
) -> dict[str, object]:
    """Single dict for CLI / bash (interval_sec None omitted in shell as empty)."""
    interval_sec, dd, di, de, derr, dinv, dsr = compute_interval_and_deltas(
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
    )
    stats_fire, stats_reason = evaluate_stats_alert(
        has_conntrack=has_conntrack,
        alert_on_stats=alert_on_stats,
        alert_on_stats_delta=alert_on_stats_delta,
        interval_sec=interval_sec,
        dd=dd,
        di=di,
        de=de,
        derr=derr,
        dinv=dinv,
        dsr=dsr,
        drop_sum=drop_sum,
        if_sum=if_sum,
        ed_sum=ed_sum,
        er_sum=er_sum,
        inv_sum=inv_sum,
        sr_sum=sr_sum,
        stats_drop_min=stats_drop_min,
        stats_insert_failed_min=stats_insert_failed_min,
        stats_delta_min_interval_sec=stats_delta_min_interval_sec,
        stats_delta_drop_min=stats_delta_drop_min,
        stats_delta_insert_failed_min=stats_delta_insert_failed_min,
        stats_delta_early_drop_min=stats_delta_early_drop_min,
        stats_delta_error_min=stats_delta_error_min,
        stats_delta_invalid_min=stats_delta_invalid_min,
        stats_delta_search_restart_min=stats_delta_search_restart_min,
        stats_rate_drop_per_min=stats_rate_drop_per_min,
        stats_rate_insert_failed_per_min=stats_rate_insert_failed_per_min,
        stats_rate_early_drop_per_min=stats_rate_early_drop_per_min,
        stats_rate_error_per_min=stats_rate_error_per_min,
        stats_rate_invalid_per_min=stats_rate_invalid_per_min,
        stats_rate_search_restart_per_min=stats_rate_search_restart_per_min,
    )
    stats_send_telegram = stats_fire and should_send_stats_alert(
        stats_last_ts, now_ts, stats_cooldown_seconds
    )
    return {
        "interval_sec": interval_sec,
        "dd": dd,
        "di": di,
        "de": de,
        "derr": derr,
        "dinv": dinv,
        "dsr": dsr,
        "stats_fire": stats_fire,
        "stats_reason": stats_reason,
        "stats_send_telegram": stats_send_telegram,
    }
