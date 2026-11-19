-- Keep the database private behind the API on managed hosts such as Supabase.
--
-- Supabase's Data API (PostgREST) exposes the public schema to its `anon` and `authenticated`
-- roles. GeoDiff never uses it: only the FastAPI service, connecting as the table owner, may
-- read or write. Row-level security with no policies denies every non-owner role, and the
-- explicit revokes remove the default grants. The owner bypasses RLS (it is not FORCEd), so
-- the API is unaffected. On plain PostgreSQL (local, CI) the roles do not exist and only RLS is
-- enabled, which is a no-op for the owner.
DO $$
DECLARE
    t text;
    r text;
BEGIN
    FOR t IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND pg_get_userbyid(c.relowner) = current_user
          AND c.relname <> 'spatial_ref_sys'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I', t, r);
            END IF;
        END LOOP;
    END LOOP;
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', r);
        END IF;
    END LOOP;
END
$$;
