-- Run ONCE as the Postgres superuser to prepare a production database.
--
--   psql -U postgres -d llm_dbms -v app_password="'change-me'" -f scripts/bootstrap_db.sql
--
-- Afterwards point POSTGRES_URI at llm_dbms_app, e.g.
--   postgresql://llm_dbms_app:change-me@host:5432/llm_dbms

\set app_password :app_password

-- pgvector must be created by a superuser; the app only does CREATE EXTENSION IF NOT EXISTS.
CREATE EXTENSION IF NOT EXISTS vector;

-- Non-superuser application role.
--   CREATEROLE : create/drop the per-tenant NOLOGIN roles
--   CREATE on DB: create/drop the per-tenant schemas
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'llm_dbms_app') THEN
    EXECUTE format('CREATE ROLE llm_dbms_app LOGIN CREATEROLE PASSWORD %L', :'app_password');
  END IF;
END $$;

GRANT CREATE, CONNECT ON DATABASE llm_dbms TO llm_dbms_app;

-- langchain_postgres creates its own tables in the public schema.
GRANT USAGE, CREATE ON SCHEMA public TO llm_dbms_app;
