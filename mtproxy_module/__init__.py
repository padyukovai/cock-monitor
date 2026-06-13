"""Compatibility shim — use cock_monitor.modules.mtproxy."""
from cock_monitor.modules.mtproxy.config import MtproxyConfig, to_bool, to_int
from cock_monitor.modules.mtproxy.repository import connect_db, init_schema

__all__ = ["MtproxyConfig", "connect_db", "init_schema", "to_bool", "to_int"]
