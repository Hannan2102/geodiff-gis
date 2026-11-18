"""Small, lazily opened connection pool. Kept small for free-tier database instances."""

from __future__ import annotations

import threading
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from geodiff.config import get_settings

DictConn = Connection[DictRow]
_pool: ConnectionPool[DictConn] | None = None
_lock = threading.Lock()


def get_pool() -> ConnectionPool[DictConn]:
    global _pool
    with _lock:
        if _pool is None:
            s = get_settings()
            kwargs: dict[str, Any] = {
                "row_factory": dict_row,
                "connect_timeout": s.db_connect_timeout_s,
                # Transaction-mode poolers (e.g. Supabase port 6543) do not support
                # server-side prepared statements.
                "prepare_threshold": None,
            }
            _pool = ConnectionPool(
                conninfo=s.database_url,
                min_size=s.db_pool_min,
                max_size=s.db_pool_max,
                connection_class=DictConn,
                kwargs=kwargs,
                open=False,
                timeout=s.db_connect_timeout_s,
            )
            _pool.open(wait=False)
        return _pool


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
