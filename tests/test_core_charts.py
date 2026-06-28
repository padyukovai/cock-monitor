"""Tests for /chart PNG time-axis formatting."""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

from cock_monitor.modules.core.charts import MSK_CHART_TZ, _chart_time_formatter


def test_chart_time_formatter_uses_msk() -> None:
    fmt = _chart_time_formatter()
    assert fmt.tz == MSK_CHART_TZ
