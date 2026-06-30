"""Tests for xray access log inbound parsing."""
from __future__ import annotations

from pathlib import Path

from cock_monitor.adapters.xray_access_log import (
    XrayAccessLogTracker,
    parse_inbound_tag,
)


def test_parse_inbound_tag() -> None:
    line = (
        "2026/06/30 19:06:25 from 92.101.207.49:34204 accepted "
        "tcp:www.google.com:443 [in-443-tcp -> direct] email: Dasha1"
    )
    assert parse_inbound_tag(line) == "in-443-tcp"
    line8443 = line.replace("in-443-tcp", "in-8443-tcp")
    assert parse_inbound_tag(line8443) == "in-8443-tcp"
    assert parse_inbound_tag("no accept here") is None


def test_access_log_tracker_inbound_counts(tmp_path: Path) -> None:
    log_path = tmp_path / "access.log"
    state_path = tmp_path / "access.state"
    log_path.write_text("", encoding="utf-8")
    tracker = XrayAccessLogTracker(inbound_tags=("in-443-tcp", "in-8443-tcp"))
    tracker.restore_state(state_path, log_path)
    assert tracker.poll().delta_accepted == 0

    log_path.write_text(
        "2026/06/30 from 1.1.1.1:1 accepted tcp:x:443 [in-443-tcp -> direct] email:a\n"
        "2026/06/30 from 2.2.2.2:2 accepted tcp:x:443 [in-8443-tcp -> usa] email:b\n"
        "2026/06/30 from 3.3.3.3:3 accepted tcp:x:443 [in-8443-tcp -> usa] email:c\n",
        encoding="utf-8",
    )
    delta = tracker.poll()
    assert delta.by_inbound == {"in-443-tcp": 1, "in-8443-tcp": 2}
    assert tracker.poll().delta_accepted == 0
