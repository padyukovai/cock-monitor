"""Incremental Xray access/error log reader for burst capture."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cock_monitor.domain.vless_traffic import extract_ip_from_from_field


@dataclass
class LogTailState:
    path: Path
    inode: int = 0
    offset: int = 0
    line_count: int = 0


@dataclass
class AccessLogDelta:
    delta_lines: int = 0
    delta_accepted: int = 0
    delta_from_ip: int = 0


@dataclass
class ErrorLogDelta:
    delta_lines: int = 0
    tail: str = ""


@dataclass
class BurstLogTracker:
    access: LogTailState | None = None
    error: LogTailState | None = None
    _error_tail_buf: list[str] = field(default_factory=list)

    def _read_new_lines(self, state: LogTailState) -> list[str]:
        if not state.path.is_file():
            return []
        try:
            st = state.path.stat()
        except OSError:
            return []
        if st.st_ino != state.inode:
            state.inode = st.st_ino
            state.offset = 0
            state.line_count = 0
        try:
            with state.path.open("rb") as f:
                f.seek(state.offset)
                raw = f.read()
                state.offset = f.tell()
        except OSError:
            return []
        if not raw:
            return []
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if text and not text.endswith("\n") and lines:
            partial = lines.pop()
            state.offset -= len(partial.encode("utf-8", errors="replace"))
        state.line_count += len(lines)
        return lines

    def poll_access(self, client_ip: str = "") -> AccessLogDelta:
        if self.access is None:
            return AccessLogDelta()
        lines = self._read_new_lines(self.access)
        accepted = sum(1 for ln in lines if "accepted" in ln)
        from_ip = 0
        if client_ip:
            for ln in lines:
                if "accepted" not in ln:
                    continue
                ip = extract_ip_from_from_field(ln)
                if ip == client_ip:
                    from_ip += 1
        return AccessLogDelta(
            delta_lines=len(lines),
            delta_accepted=accepted,
            delta_from_ip=from_ip,
        )

    def poll_error(self, tail_max_chars: int = 500) -> ErrorLogDelta:
        if self.error is None:
            return ErrorLogDelta()
        lines = self._read_new_lines(self.error)
        if lines:
            self._error_tail_buf.extend(lines)
            joined = "\n".join(self._error_tail_buf)
            if len(joined) > tail_max_chars:
                joined = joined[-tail_max_chars:]
                self._error_tail_buf = joined.splitlines()
        tail = "\n".join(self._error_tail_buf[-5:])
        if len(tail) > tail_max_chars:
            tail = tail[-tail_max_chars:]
        return ErrorLogDelta(delta_lines=len(lines), tail=tail)
