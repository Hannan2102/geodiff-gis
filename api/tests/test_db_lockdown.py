"""The database stays private behind the API, even on hosts that expose `public` (Supabase)."""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from geodiff.main import app
from geodiff.migrate import MIGRATIONS_DIR, apply_migrations

LOCKDOWN = MIGRATIONS_DIR / "0003_lock_down_public_schema.sql"


def _own_tables(conn: psycopg.Connection) -> dict[str, bool]:
    rows = conn.execute(
        """
        SELECT c.relname, c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND pg_get_userbyid(c.relowner) = current_user AND c.relname <> 'spatial_ref_sys'
        """
    ).fetchall()
    return {r[0]: r[1] for r in rows}


@pytest.mark.db
def test_every_geodiff_table_has_row_level_security() -> None:
    apply_migrations()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        tables = _own_tables(conn)
    assert {"workspaces", "datasets", "decisions", "schema_migrations"} <= set(tables)
    missing = sorted(t for t, rls in tables.items() if not rls)
    assert missing == [], f"enable RLS in a migration for: {missing}"


@pytest.mark.db
def test_supabase_style_roles_lose_access() -> None:
    """Simulate Supabase's default grants to `anon`, then apply the lockdown again."""
    apply_migrations()
    with TestClient(app) as client:
        assert client.post("/v1/workspaces").status_code == 201  # at least one row exists
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        created = not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'anon'").fetchone()
        if created:
            conn.execute("CREATE ROLE anon NOLOGIN")
        try:
            conn.execute("GRANT USAGE ON SCHEMA public TO anon")
            conn.execute("GRANT SELECT, INSERT ON public.workspaces, public.datasets TO anon")
            conn.execute(LOCKDOWN.read_text())  # idempotent
            for t in ("workspaces", "datasets"):
                row = conn.execute(
                    "SELECT has_table_privilege('anon', %s, 'SELECT')", (f"public.{t}",)
                ).fetchone()
                assert row is not None and row[0] is False
            # Even with a grant re-added by hand, RLS without policies returns nothing.
            conn.execute("GRANT SELECT ON public.workspaces TO anon")
            with conn.transaction():
                conn.execute("SET LOCAL ROLE anon")
                assert conn.execute("SELECT count(*) FROM public.workspaces").fetchone() == (0,)
        finally:
            conn.execute("RESET ROLE")
            if created:
                conn.execute("DROP OWNED BY anon")
                conn.execute("DROP ROLE anon")
