"""Incremental Xray access.log reader with per-inbound accept counts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cock_monitor.adapters.burst_access_log import LogTailState

_INBOUND_TAG_RE = re.compile(r"\[(in-[\w-]+)")


def parse_inbound_tag(line: str) -> str | None:
    """Extract inbound tag from an access.log accept line, e.g. in-443-tcp."""
    if "accepted" not in line:
        return None
    match = _INBOUND_TAG_RE.search(line)
    return match.group(1) if match else None


@dataclass
class InboundAcceptDelta:
    delta_lines: int = 0
    by_inbound: dict[str, int] = field(default_factory=dict)

    @property
    def delta_accepted(self) -> int:
        return sum(self.by_inbound.values())


@dataclass
class XrayAccessLogTracker:
    state: LogTailState | None = None
    inbound_tags: tuple[str, ...] = ()

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
        tmp = state_path.parent / f".xray-access-state.{state_path.name}.tmp"
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

    def poll(self) -> InboundAcceptDelta:
        lines = self._read_new_lines()
        counts: dict[str, int] = {}
        tag_filter = set(self.inbound_tags) if self.inbound_tags else None
        for line in lines:
            tag = parse_inbound_tag(line)
            if tag is None:
                continue
            if tag_filter is not None and tag not in tag_filter:
                continue
            counts[tag] = counts.get(tag, 0) + 1
        return InboundAcceptDelta(delta_lines=len(lines), by_inbound=counts)
