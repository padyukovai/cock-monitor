"""Incremental xray error.log reader with hop-relevant pattern classification."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cock_monitor.adapters.burst_access_log import LogTailState

_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mux_fail", re.compile(r"failed to handler mux", re.IGNORECASE)),
    ("conn_refused", re.compile(r"connection refused", re.IGNORECASE)),
    ("retry_exhausted", re.compile(r"all retry attempts failed", re.IGNORECASE)),
)


@dataclass
class ErrorLogDelta:
    delta_lines: int = 0
    delta_total: int = 0
    delta_mux_fail: int = 0
    delta_conn_refused: int = 0
    delta_retry_exhausted: int = 0
    tail: str = ""


@dataclass
class XrayErrorLogTracker:
    state: LogTailState | None = None
    _tail_buf: list[str] = field(default_factory=list)

    def restore_state(self, state_path: Path, log_path: Path) -> None:
        if not log_path.is_file():
            return
        self.state = LogTailState(path=log_path)
        inode = offset = None
        if state_path.is_file():
            for line in state_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("inode="):
                    try:
                        inode = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
                elif line.startswith("offset="):
                    try:
                        offset = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
        try:
            st = log_path.stat()
            self.state.inode = st.st_ino
            if inode == st.st_ino and offset is not None:
                self.state.offset = min(offset, st.st_size)
            else:
                self.state.offset = st.st_size
        except OSError:
            pass

    def save_state(self, state_path: Path) -> None:
        if self.state is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        text = f"inode={self.state.inode}\noffset={self.state.offset}\n"
        tmp = state_path.parent / f".xray-error-state.{state_path.name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(state_path)

    def _read_new_lines(self) -> list[str]:
        if self.state is None or not self.state.path.is_file():
            return []
        try:
            st = self.state.path.stat()
        except OSError:
            return []
        if st.st_ino != self.state.inode:
            self.state.inode = st.st_ino
            self.state.offset = 0
            self.state.line_count = 0
        try:
            with self.state.path.open("rb") as f:
                f.seek(self.state.offset)
                raw = f.read()
                self.state.offset = f.tell()
        except OSError:
            return []
        if not raw:
            return []
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if text and not text.endswith("\n") and lines:
            partial = lines.pop()
            self.state.offset -= len(partial.encode("utf-8", errors="replace"))
        self.state.line_count += len(lines)
        return lines

    def poll(self, *, tail_max_chars: int = 500) -> ErrorLogDelta:
        lines = self._read_new_lines()
        counts = {key: 0 for key, _ in _ERROR_PATTERNS}
        counts["total"] = 0
        for line in lines:
            matched = False
            for key, pat in _ERROR_PATTERNS:
                if pat.search(line):
                    counts[key] += 1
                    matched = True
            if matched:
                counts["total"] += 1
        if lines:
            self._tail_buf.extend(lines[-5:])
            joined = "\n".join(self._tail_buf)
            if len(joined) > tail_max_chars:
                joined = joined[-tail_max_chars:]
                self._tail_buf = joined.splitlines()
        tail = "\n".join(self._tail_buf[-3:])
        if len(tail) > tail_max_chars:
            tail = tail[-tail_max_chars:]
        return ErrorLogDelta(
            delta_lines=len(lines),
            delta_total=counts["total"],
            delta_mux_fail=counts["mux_fail"],
            delta_conn_refused=counts["conn_refused"],
            delta_retry_exhausted=counts["retry_exhausted"],
            tail=tail,
        )
