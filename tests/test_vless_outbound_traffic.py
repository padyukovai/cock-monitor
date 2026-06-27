"""Unit tests for outbound hop traffic collection and reporting."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from cock_monitor.adapters.vless_outbound_traffic import (
    collect_outbound_traffic_rows,
    outbound_rows_to_maps,
    resolve_hop_tags,
)
from cock_monitor.adapters.xray_stats import query_outbound_traffic_stats
from cock_monitor.adapters.xui_sqlite import OutboundTrafficRow, fetch_outbound_traffics
from cock_monitor.domain import vless_traffic as vt


def test_build_report_includes_outbound_hops_section() -> None:
    text, _, _, _, _ = vt.build_report(
        host="rf3",
        title="daily",
        subtitle="sub",
        current_map={"u1": 2000},
        prev_map={"u1": 1000},
        top_n=5,
        abuse_gb=100.0,
        abuse_share_pct=50.0,
        min_total_mb=1,
        outbound_up={"germany": 300, "usa": 100},
        outbound_down={"germany": 1200, "usa": 400},
        outbound_total={"germany": 1500, "usa": 500},
        prev_outbound_up={"germany": 100, "usa": 50},
        prev_outbound_down={"germany": 400, "usa": 100},
        prev_outbound_total={"germany": 500, "usa": 150},
        hop_tags={"germany", "usa"},
    )
    assert "<b>Outbound hops</b>:" in text
    assert "germany" in text
    assert "usa" in text
    assert "direct" not in text


def test_compute_outbound_delta_entries_filters_system_tags() -> None:
    entries = vt.compute_outbound_delta_entries(
        {"direct": 10, "germany": 100},
        {"direct": 20, "germany": 900},
        {"direct": 30, "germany": 1000},
        {"direct": 0, "germany": 0},
        {"direct": 0, "germany": 0},
        {"direct": 0, "germany": 0},
    )
    assert len(entries) == 1
    assert entries[0].tag == "germany"
    assert entries[0].delta_total == 1000


def test_fetch_outbound_traffics_reads_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "xui.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE outbound_traffics (
            id INTEGER PRIMARY KEY,
            tag TEXT,
            up INTEGER,
            down INTEGER,
            total INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO outbound_traffics (tag, up, down, total) VALUES ('germany', 100, 200, 300)"
    )
    conn.commit()
    rows = fetch_outbound_traffics(conn)
    conn.close()
    assert len(rows) == 1
    assert rows[0].tag == "germany"
    assert rows[0].total == 300


def test_collect_outbound_traffic_rows_prefers_db(tmp_path: Path) -> None:
    db = tmp_path / "xui.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE outbound_traffics (
            id INTEGER PRIMARY KEY,
            tag TEXT,
            up INTEGER,
            down INTEGER,
            total INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO outbound_traffics (tag, up, down, total) VALUES ('germany', 10, 20, 30)"
    )
    conn.commit()
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"outbounds": [{"tag": "germany"}, {"tag": "direct"}]}),
        encoding="utf-8",
    )
    env = {
        "HOP_LINKS": "germany:dst:1.2.3.4:10089",
        "VLESS_XRAY_CONFIG_PATH": str(cfg),
    }
    rows, hop_tags, source = collect_outbound_traffic_rows(conn, env=env)
    conn.close()
    assert source == "db"
    assert hop_tags == {"germany"}
    assert rows[0].total == 30


def test_query_outbound_traffic_stats_parses_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "xray"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    payload = {
        "stat": [
            {"name": "outbound>>>germany>>>traffic>>>uplink", "value": 100},
            {"name": "outbound>>>germany>>>traffic>>>downlink", "value": 400},
            {"name": "inbound>>>in-443>>>traffic>>>downlink", "value": 999},
        ]
    }

    def _fake_run(cmd, **kwargs):
        class _Result:
            returncode = 0
            stdout = json.dumps(payload)
            stderr = ""

        return _Result()

    monkeypatch.setattr("cock_monitor.adapters.xray_stats.subprocess.run", _fake_run)
    result = query_outbound_traffic_stats(api_addr="127.0.0.1:62789", xray_bin=str(fake_bin))
    assert result.error == ""
    assert len(result.rows) == 1
    assert result.rows[0].tag == "germany"
    assert result.rows[0].up == 100
    assert result.rows[0].down == 400


def test_resolve_hop_tags_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"outbounds": [{"tag": "germany"}, {"tag": "usa"}, {"tag": "direct"}]}),
        encoding="utf-8",
    )
    tags = resolve_hop_tags({}, config_path=str(cfg))
    assert tags == {"germany", "usa"}


def test_outbound_rows_to_maps() -> None:
    up, down, total = outbound_rows_to_maps(
        [
            OutboundTrafficRow(tag="germany", up=1, down=2),
            OutboundTrafficRow(tag="usa", up=3, down=4),
        ]
    )
    assert total == {"germany": 3, "usa": 7}
    assert up == {"germany": 1, "usa": 3}
    assert down == {"germany": 2, "usa": 4}
