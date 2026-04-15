from __future__ import annotations

from datetime import timedelta, timezone

MSK_TZ = timezone(timedelta(hours=3), name="MSK")


def format_bytes(n: int) -> str:
    val = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            if unit == "B":
                return f"{int(val)} {unit}"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"
