"""Open METRICS_DB and migrate schemas for all enabled modules."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cock_monitor.platform.registry import get_registry, parse_enabled_modules
from cock_monitor.storage.sqlite_connection import open_sqlite_connection

_SCHEMA_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_versions (
  module TEXT PRIMARY KEY NOT NULL,
  version INTEGER NOT NULL
);
"""


class StorageManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def open(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return open_sqlite_connection(str(self.db_path))

    def migrate_all(self, env: dict[str, str]) -> None:
        registry = get_registry()
        enabled = parse_enabled_modules(env)
        conn = self.open()
        try:
            conn.executescript(_SCHEMA_VERSIONS_DDL)
            for mid in enabled:
                spec = registry.get(mid)
                if spec.schema_migrate is not None:
                    spec.schema_migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def wipe(self) -> None:
        if self.db_path.is_file():
            self.db_path.unlink()
