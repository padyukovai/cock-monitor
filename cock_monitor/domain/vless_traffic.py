"""Pure VLESS traffic report logic: access.log aggregation and HTML report body."""
from __future__ import annotations

import html
import ipaddress
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone, tzinfo
from pathlib import Path

try:
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover - python < 3.9 fallback
    ZoneInfo = None  # type: ignore[misc,assignment]


def load_tz(tz_name: str) -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    if tz_name == "Europe/Moscow":
        return timezone(timedelta(hours=3), name="MSK")
    if tz_name == "Asia/Tehran":
        return timezone(timedelta(hours=3, minutes=30), name="IRST")
    raise ValueError(f"invalid or unsupported timezone: {tz_name!r}")


@dataclass(frozen=True)
class IpParseStats:
    elapsed_ms: int
    bytes_read: int
    lines_matched: int
    truncated: bool


def read_file_slice(path: Path, max_bytes: int, *, from_tail: bool) -> tuple[bytes, bool]:
    """Read up to max_bytes from file start (daily) or end (since-last). Returns (data, truncated)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= max_bytes:
                f.seek(0)
                return f.read(), False
            if from_tail:
                start = max(0, size - max_bytes)
                f.seek(start)
                data = f.read()
                truncated = start > 0
                if truncated and b"\n" in data:
                    nl = data.find(b"\n")
                    data = data[nl + 1 :]
                return data, truncated
            f.seek(0)
            data = f.read(max_bytes)
            return data, True
    except OSError:
        return b"", False


def parse_access_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def extract_ip_from_from_field(line: str) -> str | None:
    idx = line.find(" from ")
    if idx < 0:
        return None
    rest = line[idx + 6 :].strip()
    if not rest:
        return None
    token = rest.split()[0]
    if token.startswith(("tcp:", "udp:")):
        parts = token.split(":", 1)
        if len(parts) < 2:
            return None
        token = parts[1]
    if token.startswith("["):
        close = token.find("]")
        if close < 0:
            return None
        return token[1:close].strip()
    if ":" in token:
        host, _, _ = token.rpartition(":")
        return host.strip() or None
    return None


def extract_access_email(line: str) -> str | None:
    if " email:" not in line:
        return None
    tail = line.rsplit(" email:", 1)[-1].strip()
    if not tail:
        return None
    return tail.split()[0].strip()


def normalize_client_ip(raw: str) -> tuple[str, str] | None:
    """Return (family, value) where family is '4' or '6' and value is normalized string."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if addr.version == 4:
        return ("4", str(addr))
    mapped = ipaddress.IPv6Address(addr).ipv4_mapped
    if mapped is not None:
        return ("4", str(mapped))
    return ("6", str(ipaddress.IPv6Address(addr).compressed))


def aggregate_vless_access_ips(
    paths: list[Path],
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    window_left_exclusive: bool,
    log_tz: tzinfo,
    allowed_emails: set[str],
    max_bytes_per_file: int,
    read_from_tail: bool,
) -> tuple[dict[str, tuple[set[str], set[str]]], IpParseStats]:
    """Aggregate unique IPv4/IPv6 per email from Xray access.log-style lines."""
    t0 = time.perf_counter()
    combined: dict[str, tuple[set[str], set[str]]] = {}
    bytes_read = 0
    lines_matched = 0
    truncated_any = False

    for path in paths:
        if not path.is_file():
            continue
        raw, truncated = read_file_slice(path, max_bytes_per_file, from_tail=read_from_tail)
        bytes_read += len(raw)
        truncated_any = truncated_any or truncated
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if " email:" not in line:
                continue
            ts_end = line.find(" from ")
            if ts_end < 0:
                continue
            ts_raw = line[:ts_end].strip()
            dt_naive = parse_access_ts(ts_raw)
            if dt_naive is None:
                continue
            if dt_naive.tzinfo is not None:
                dt_naive = dt_naive.replace(tzinfo=None)
            log_dt = dt_naive.replace(tzinfo=log_tz).astimezone(timezone.utc)
            if window_left_exclusive:
                if log_dt > window_end_utc:
                    break
                if not (log_dt > window_start_utc and log_dt <= window_end_utc):
                    continue
            else:
                if log_dt >= window_end_utc:
                    break
                if not (log_dt >= window_start_utc and log_dt < window_end_utc):
                    continue
            email = extract_access_email(line)
            if not email or email not in allowed_emails:
                continue
            ip_raw = extract_ip_from_from_field(line)
            if not ip_raw:
                continue
            norm = normalize_client_ip(ip_raw)
            if norm is None:
                continue
            fam, val = norm
            if email not in combined:
                combined[email] = (set(), set())
            v4s, v6s = combined[email]
            if fam == "4":
                v4s.add(val)
            else:
                v6s.add(val)
            lines_matched += 1

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return combined, IpParseStats(
        elapsed_ms=elapsed_ms,
        bytes_read=bytes_read,
        lines_matched=lines_matched,
        truncated=truncated_any,
    )


def daily_window_utc(prev_day_iso: str, snapshot_day_iso: str, tz_daily: tzinfo) -> tuple[datetime, datetime]:
    d0 = date.fromisoformat(prev_day_iso)
    d1 = date.fromisoformat(snapshot_day_iso)
    start = datetime.combine(d0, dt_time.min, tzinfo=tz_daily).astimezone(timezone.utc)
    end = datetime.combine(d1, dt_time.min, tzinfo=tz_daily).astimezone(timezone.utc)
    return start, end


def shrink_telegram_html(text: str, max_len: int = 4000) -> str:
    if len(text) <= max_len:
        return text
    cut = re.sub(
        r"\n\n<b>Top \d+ by unique IP.*?(?=\n\n<b>Potential heavy|\n\n<i>Note:|\Z)",
        "",
        text,
        flags=re.S,
    )
    if len(cut) > max_len:
        cut = re.sub(
            r"\n\n<b>Potential heavy downloaders</b>:.*?(?=\n\n<i>Note:|\Z)",
            "",
            cut,
            flags=re.S,
        )
    if len(cut) > max_len:
        cut = cut[: max_len - 3] + "..."
    return cut


def fmt_bytes(num: int) -> str:
    if num < 0:
        num = 0
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(num)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx <= 1:
        return f"{int(value)} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def build_report(
    *,
    host: str,
    title: str,
    subtitle: str,
    current_map: dict[str, int],
    prev_map: dict[str, int],
    top_n: int,
    abuse_gb: float,
    abuse_share_pct: float,
    min_total_mb: int,
    ip_counts: dict[str, tuple[int, int]] | None = None,
    ip_top_k: int = 3,
    ip_truncated: bool = False,
) -> tuple[str, int, int, str, int]:
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    if not prev_map:
        text = (
            f"<b>{esc(host)} — {esc(title)}</b>\n"
            f"<i>{esc(subtitle)}</i>\n\n"
            "Baseline recorded now. Report will start from the next successful send."
        )
        return text, 0, 0, "", 0

    deltas: list[tuple[str, int, bool]] = []
    total_delta = 0
    reset_count = 0
    for email, cur_total in current_map.items():
        prev_total = prev_map.get(email, 0)
        raw_delta = cur_total - prev_total
        reset_detected = raw_delta < 0
        delta = raw_delta if raw_delta > 0 else 0
        if reset_detected:
            reset_count += 1
        if delta > 0:
            total_delta += delta
        deltas.append((email, delta, reset_detected))

    deltas.sort(key=lambda x: x[1], reverse=True)
    active = sum(1 for _, d, _ in deltas if d > 0)
    top = deltas[: max(1, top_n)]
    top1_email = top[0][0] if top else ""
    top1_delta = top[0][1] if top else 0
    top1_share = (top1_delta * 100.0 / total_delta) if total_delta > 0 else 0.0

    delta_lookup: dict[str, int] = {e: d for e, d, _ in deltas}

    lines: list[str] = []
    lines.append(f"<b>{esc(host)} — {esc(title)}</b>")
    lines.append(f"<i>{esc(subtitle)}</i>")
    lines.append(
        f"\n<b>Total:</b> <code>{fmt_bytes(total_delta)}</code> | "
        f"<b>Active clients:</b> <code>{active}</code> | "
        f"<b>Top1 share:</b> <code>{top1_share:.1f}%</code>"
    )

    lines.append("")
    lines.append(f"<b>Top {max(1, top_n)} downloaders</b>:")
    rank = 0
    for email, delta, _ in top:
        if delta <= 0:
            continue
        rank += 1
        share = (delta * 100.0 / total_delta) if total_delta > 0 else 0.0
        ip_suffix = ""
        if ip_counts is not None:
            n4, n6 = ip_counts.get(email, (0, 0))
            ip_suffix = (
                f" | <b>IP4</b> <code>{n4}</code> <b>IP6</b> <code>{n6}</code> "
                f"<b>IPΣ</b> <code>{n4 + n6}</code>"
            )
        lines.append(
            f"{rank}) <code>{esc(email)}</code> — <b>{fmt_bytes(delta)}</b> "
            f"(<code>{share:.1f}%</code>){ip_suffix}"
        )
    if rank == 0:
        lines.append("No positive usage detected for this day.")

    if ip_counts is not None and ip_top_k > 0:
        ip_candidates: list[tuple[str, int, int, int, int]] = []
        for email, (n4, n6) in ip_counts.items():
            sigma = n4 + n6
            if sigma <= 0:
                continue
            ip_candidates.append(
                (email, sigma, n4, n6, int(delta_lookup.get(email, 0)))
            )
        ip_candidates.sort(key=lambda x: (-x[1], -x[4]))
        top_ip = ip_candidates[: max(1, ip_top_k)]
        if top_ip:
            lines.append("")
            lines.append(
                f"<b>Top {max(1, ip_top_k)} by unique IP (IPv4+IPv6)</b>:"
            )
            for i, (email, sigma, n4, n6, dlt) in enumerate(top_ip, start=1):
                lines.append(
                    f"{i}) <code>{esc(email)}</code> — <b>IPΣ</b> <code>{sigma}</code> "
                    f"(<b>IP4</b> <code>{n4}</code> <b>IP6</b> <code>{n6}</code>) "
                    f"| <b>traffic Δ</b> <code>{fmt_bytes(dlt)}</code>"
                )
        if ip_truncated:
            lines.append("")
            lines.append(
                "<i>Note: IP log tail truncated by VLESS_IP_PARSE_MAX_MB; counts may be incomplete.</i>"
            )

    threshold_bytes = int(abuse_gb * 1024 * 1024 * 1024)
    min_total_bytes = int(min_total_mb * 1024 * 1024)
    abuse: list[str] = []
    for email, delta, _ in deltas:
        if delta <= 0:
            continue
        share = (delta * 100.0 / total_delta) if total_delta > 0 else 0.0
        by_abs = threshold_bytes > 0 and delta >= threshold_bytes
        by_share = (
            abuse_share_pct > 0
            and total_delta >= min_total_bytes
            and share >= abuse_share_pct
        )
        if by_abs or by_share:
            abuse.append(
                f"- <code>{esc(email)}</code>: <b>{fmt_bytes(delta)}</b> "
                f"(<code>{share:.1f}%</code>)"
            )

    if abuse:
        lines.append("")
        lines.append("<b>Potential heavy downloaders</b>:")
        lines.extend(abuse)

    if reset_count > 0:
        lines.append("")
        lines.append(
            f"<i>Note: reset/anomaly detected for {reset_count} client(s), "
            "negative deltas clamped to 0.</i>"
        )

    text = "\n".join(lines)
    text = shrink_telegram_html(text, max_len=4000)
    return text, active, total_delta, top1_email, top1_delta
