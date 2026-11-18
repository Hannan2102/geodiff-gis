"""Forward-only SQL migrations guarded by a Postgres advisory lock.

Run as a release step: ``python -m geodiff.migrate``. Concurrent runners (e.g. two
instances restarting together) serialize on the lock, and each file applies inside its
own transaction, so a crash mid-file leaves the database at the previous version.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from pathlib import Path

import psycopg

from geodiff.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LOCK_KEY = 0x6E0D1FF  # arbitrary, stable advisory-lock key for GeoDiff migrations

log = logging.getLogger("geodiff.migrate")


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def connect_with_retry(url: str, attempts: int = 10, base_delay: float = 0.5) -> psycopg.Connection:
    """Connect with bounded exponential backoff (the database may still be starting)."""
    for i in range(attempts):
        try:
            return psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=5)
        except psycopg.OperationalError:
            if i == attempts - 1:
                raise
            delay = min(base_delay * 2**i, 8.0)
            log.warning("database not reachable yet; retrying in %.1fs", delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def apply_migrations(database_url: str | None = None) -> list[str]:
    url = database_url or get_settings().database_url
    applied: list[str] = []
    with connect_with_retry(url) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            done = {
                r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")
            }
            for path in migration_files():
                sql = path.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                version = path.stem
                if version in done:
                    if done[version] != checksum:
                        raise RuntimeError(
                            f"Migration {version} changed after it was applied; "
                            "add a new migration instead of editing an applied one."
                        )
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (version, checksum),
                    )
                applied.append(version)
                log.info("applied migration %s", version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return applied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    applied = apply_migrations()
    print(f"migrations applied: {applied or 'none (up to date)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
