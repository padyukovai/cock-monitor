"""DDL/DML for conntrack_samples and host_samples (via migrations_conntrack_host)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from cock_monitor.storage.migrations_conntrack_host import migrate_conntrack_host


@dataclass(frozen=True)
class ConntrackSampleInsert:
    ts: int
    fill_pct: int | None
    fill_count: int | None
    fill_max: int | None
    drop: int
    insert_failed: int
    early_drop: int
    error: int
    invalid: int
    search_restart: int
    interval_sec: int | None
    delta_drop: int | None
    delta_insert_failed: int | None
    delta_early_drop: int | None
    delta_error: int | None
    delta_invalid: int | None
    delta_search_restart: int | None


@dataclass(frozen=True)
class HostSampleInsert:
    ts: int
    load1: float | None
    mem_avail_kb: int | None
    swap_used_kb: int | None
    tcp_inuse: int | None
    tcp_orphan: int | None
    tcp_tw: int | None
    tcp6_inuse: int | None
    shaper_rate_mbit: float | None
    shaper_cpu_pct: int | None
    tc_qdisc_root: str | None


class ConntrackHostRepository:
    """All writes to conntrack_samples / host_samples should go through this class."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    @contextmanager
    def open(cls, db_path: str | Path) -> Iterator[ConntrackHostRepository]:
        path = Path(db_path)
        conn = sqlite3.connect(str(path), timeout=60.0)
        # Avoid implicit DEFERRED transactions so BEGIN IMMEDIATE in inserts works.
        conn.isolation_level = None
        try:
            migrate_conntrack_host(conn)
            yield cls(conn)
        finally:
            conn.close()

    def migrate(self) -> None:
        migrate_conntrack_host(self._conn)

    def read_last_stats_line(self) -> str | None:
        """Same columns/order as legacy metrics_read_last (pipe-separated)."""
        cur = self._conn.execute(
            """
            SELECT ts, "drop", insert_failed, early_drop, "error", invalid, search_restart
            FROM conntrack_samples
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        return "|".join(str(x) for x in row)

    def insert_sample_and_host(
        self,
        sample: ConntrackSampleInsert,
        host: HostSampleInsert,
    ) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                """
                INSERT INTO conntrack_samples (
                  ts, fill_pct, fill_count, fill_max,
                  "drop", insert_failed, early_drop, "error", invalid, search_restart,
                  interval_sec,
                  delta_drop, delta_insert_failed, delta_early_drop, delta_error,
                  delta_invalid, delta_search_restart
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sample.ts,
                    sample.fill_pct,
                    sample.fill_count,
                    sample.fill_max,
                    sample.drop,
                    sample.insert_failed,
                    sample.early_drop,
                    sample.error,
                    sample.invalid,
                    sample.search_restart,
                    sample.interval_sec,
                    sample.delta_drop,
                    sample.delta_insert_failed,
                    sample.delta_early_drop,
                    sample.delta_error,
                    sample.delta_invalid,
                    sample.delta_search_restart,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO host_samples (
                  ts, load1, mem_avail_kb, swap_used_kb,
                  tcp_inuse, tcp_orphan, tcp_tw, tcp6_inuse,
                  shaper_rate_mbit, shaper_cpu_pct, tc_qdisc_root
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    host.ts,
                    host.load1,
                    host.mem_avail_kb,
                    host.swap_used_kb,
                    host.tcp_inuse,
                    host.tcp_orphan,
                    host.tcp_tw,
                    host.tcp6_inuse,
                    host.shaper_rate_mbit,
                    host.shaper_cpu_pct,
                    host.tc_qdisc_root,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def apply_retention(self, cutoff_ts: int) -> None:
        self._conn.execute(
            "DELETE FROM conntrack_samples WHERE ts < ?", (cutoff_ts,)
        )
        self._conn.execute("DELETE FROM host_samples WHERE ts < ?", (cutoff_ts,))
        self._conn.commit()

    def trim_to_max_rows(self, max_rows: int) -> None:
        self._conn.execute(
            """
            DELETE FROM conntrack_samples
            WHERE id NOT IN (
              SELECT id FROM conntrack_samples ORDER BY id DESC LIMIT ?
            )
            """,
            (max_rows,),
        )
        self._conn.commit()

    def delete_host_orphans(self) -> None:
        self._conn.execute(
            """
            DELETE FROM host_samples
            WHERE ts NOT IN (SELECT ts FROM conntrack_samples)
            """
        )
        self._conn.commit()
